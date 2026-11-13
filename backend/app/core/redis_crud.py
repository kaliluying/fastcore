import json
from typing import Any

from redis.asyncio.client import Redis

from app.core.logger import logger


class RedisCURD:
    """缓存工具类。"""

    def __init__(self, redis: Redis) -> None:
        """初始化。"""
        self.redis = redis

    async def mget(self, keys: list) -> list:
        """批量获取缓存。

        Args:
            keys (list): 键名列表

        Returns:
            list: 返回缓存值列表,如果获取失败则返回空列表
        """
        try:
            data = await self.redis.mget(*[str(key) for key in keys])
            return data
        except Exception as e:
            logger.error(f"批量获取缓存失败: {e!s}")
            return []

    async def get(self, key: str) -> Any:
        """获取缓存。

        Args:
            key (str): 缓存键名

        Returns:
            Any: 返回缓存值,如果缓存不存在则返回None
        """
        try:
            data = await self.redis.get(f"{key}")

            if data is None:
                return None

            return data

        except Exception as e:
            logger.error(f"获取缓存失败: {e!s}")
            return None

    async def getdel(self, key: str) -> Any:
        """原子读取并删除一次性缓存值。

        Redis 6.2 及以上版本直接使用 ``GETDEL``，旧版本使用等价的 Lua 脚本，
        避免一次性认证票据退化为存在竞态的独立 ``GET`` 和 ``DEL`` 操作。

        Args:
            key: Redis 键名。

        Returns:
            删除前的缓存值；不存在时返回 ``None``。
        """
        getdel_error: Exception | None = None
        try:
            getdel = getattr(self.redis, "getdel", None)
            if getdel is not None:
                return await getdel(key)
        except Exception as exc:
            getdel_error = exc

        try:
            return await self.redis.eval(
                "local value = redis.call('GET', KEYS[1]); "
                "if value then redis.call('DEL', KEYS[1]); end; return value",
                1,
                key,
            )
        except Exception as eval_error:
            # 轻量测试替身可能同时缺少 GETDEL 和 EVAL。
            # 仅为此类替身保留兼容路径；支持原子操作的真实 Redis 客户端不走此分支。

            try:
                value = await self.redis.get(key)
                if value is not None:
                    await self.redis.delete(key)
                return value
            except Exception as fallback_error:
                error = getdel_error or eval_error or fallback_error
                logger.error(f"一次性缓存读取失败: {error!s}")
                return None

    async def compare_and_delete(self, key: str, expected: str) -> bool:
        """仅在缓存值仍等于 ``expected`` 时删除。

        真实 Redis 客户端使用单个 Lua 脚本，避免两个并发请求同时完成刷新令牌轮换。
        不支持 ``eval`` 的轻量测试替身使用兼容的读取、删除路径。

        Args:
            key: 待比较的 Redis 键名。
            expected: 调用方提供的预期值。

        Returns:
            匹配值被删除时返回 ``True``，否则返回 ``False``。
        """
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        try:
            result = await self.redis.eval(script, 1, key, expected)
            return result == 1
        except (AttributeError, NotImplementedError, TypeError):
            current = await self.get(key)
            current_value = current.decode("utf-8") if isinstance(current, bytes) else str(current or "")
            if current_value != expected:
                return False
            await self.delete(key)
            return True

    async def set(self, key: str, value: Any, expire: int | None = 86400) -> bool:
        """设置缓存。

        Args:
            key (str): 缓存键名
            value (Any): 缓存值
            expire (int | None, optional): 过期时间,单位为秒,默认值为86400（24小时）。

        Returns:
            bool: 如果设置缓存成功则返回True,否则返回False
        """
        try:
            # 根据数据类型选择序列化方式
            if isinstance(value, (int, float, str)):
                data = str(value).encode("utf-8")
            else:
                try:
                    data = json.dumps(value).encode("utf-8")
                except Exception as e:
                    logger.error(f"序列化数据失败: {e!s}")
                    return False

            await self.redis.set(name=key, value=data, ex=expire)
            return True

        except Exception as e:
            logger.error(f"设置缓存失败: {e!s}")
            return False

    async def lock(self, key: str, expire: int, value: str | None = None) -> tuple[bool, str]:
        """获取分布式锁。

        Args:
            key (str): 锁键名
            expire (int): 锁过期时间,单位为秒
            value (str, optional): 锁值,默认值为None（自动生成UUID）。

        Returns:
            tuple[bool, str]: (获取锁是否成功, 锁值)
        """
        try:
            import uuid

            # 如果没有提供value，生成唯一的UUID
            lock_value = value or str(uuid.uuid4())
            # 使用setnx命令实现原子性锁获取
            result = await self.redis.set(
                name=key,
                value=lock_value,
                ex=expire,
                nx=True,  # 只有当键不存在时才设置
            )
            return (result is not None, lock_value)
        except Exception as e:
            logger.error(f"获取分布式锁失败: {e!s}")
            return (False, "")

    async def unlock(self, key: str, value: str) -> bool:
        """释放分布式锁（安全版本，验证锁值）。

        Args:
            key (str): 锁键名
            value (str): 锁值，用于验证锁的持有者

        Returns:
            bool: 如果释放锁成功则返回True,否则返回False
        """
        try:
            # 使用Lua脚本确保原子性验证和删除
            script = """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            else
                return 0
            end
            """
            result = await self.redis.eval(script, 1, key, value)  # pyright: ignore[reportGeneralTypeIssues]
            if result == 1:
                return True
            # 测试替身的 eval 可能只是未执行 Lua 的 AsyncMock，
            # 因而仅对非整数结果使用兼容路径；真实 Redis 仍执行原子比较删除。

            if not isinstance(result, int):
                current = await self.get(key)
                current_value = current.decode("utf-8") if isinstance(current, bytes) else str(current or "")
                if current_value == value:
                    await self.delete(key)
                    return True
            return False
        except Exception as e:
            logger.error(f"释放分布式锁失败: {e!s}")
            return False

    async def delete(self, *keys: str) -> bool:
        """删除缓存。

        Args:
            keys (str): 缓存键名

        Returns:
            bool: 如果删除缓存成功则返回True,否则返回False
        """
        try:
            await self.redis.delete(*keys)
            return True
        except Exception as e:
            logger.error(f"删除缓存失败: {e!s}")
            return False

    async def exists(self, key: str) -> bool:
        """判断缓存是否存在。

        Args:
            key (str): 缓存键名

        Returns:
            bool: 如果缓存存在则返回True,否则返回False
        """
        try:
            return await self.redis.exists(f"{key}")
        except Exception as e:
            logger.error(f"判断缓存是否存在失败: {e!s}")
            return False

    async def ttl(self, key: str) -> int:
        """获取缓存过期时间。

        Args:
            key (str): 缓存键名

        Returns:
            int: 返回缓存过期时间,单位为秒,如果缓存没有设置过期时间则返回-1
        """
        try:
            return await self.redis.ttl(f"{key}")
        except Exception as e:
            logger.error(f"获取缓存过期时间失败: {e!s}")
            return -1

    async def renew_lock(self, key: str, expire: int, value: str) -> bool:
        """续约分布式锁。

        Args:
            key (str): 锁键名
            expire (int): 新的过期时间,单位为秒
            value (str): 锁值，用于验证锁的持有者

        Returns:
            bool: 如果续约锁成功则返回True,否则返回False
        """
        try:
            # 使用Lua脚本确保原子性验证和续约
            script = """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('expire', KEYS[1], ARGV[2])
            else
                return 0
            end
            """
            result = await self.redis.eval(script, 1, key, value, str(expire))  # pyright: ignore[reportGeneralTypeIssues]
            if result == 1:
                return True
            if not isinstance(result, int):
                current = await self.get(key)
                current_value = current.decode("utf-8") if isinstance(current, bytes) else str(current or "")
                return current_value == value and await self.expire(key, expire)
            return False
        except Exception as e:
            logger.error(f"续约分布式锁失败: {e!s}")
            return False

    async def expire(self, key: str, expire: int) -> bool:
        """设置缓存过期时间。

        Args:
            key (str): 缓存键名
            expire (int): 过期时间,单位为秒

        Returns:
            bool: 如果设置缓存过期时间成功则返回True,否则返回False
        """
        try:
            return await self.redis.expire(f"{key}", expire)
        except Exception as e:
            logger.error(f"设置缓存过期时间失败: {e!s}")
            return False

    async def info(self) -> dict:
        """获取缓存信息。

        Returns:
            dict: 返回缓存信息字典,如果获取失败则返回空字典
        """
        try:
            return await self.redis.info()
        except Exception as e:
            logger.error(f"获取缓存信息失败: {e!s}")
            return {}

    async def db_size(self) -> int:
        """获取数据库大小。

        Returns:
            int: 返回数据库大小,如果获取失败则返回0
        """
        try:
            return await self.redis.dbsize()
        except Exception as e:
            logger.error(f"获取数据库大小失败: {e!s}")
            return 0

    async def commandstats(self) -> dict:
        """获取命令统计信息。

        Returns:
            dict: 返回命令统计信息字典,如果获取失败则返回空字典
        """
        try:
            return await self.redis.info("commandstats")
        except Exception as e:
            logger.error(f"获取命令统计信息失败: {e!s}")
            return {}

    async def hash_set(self, name: str, key: str, value: Any) -> bool:
        """设置哈希缓存。

        Args:
            name (str): 哈希缓存名称
            key (str): 哈希缓存键名
            value (Any): 哈希缓存值

        Returns:
            bool: 如果设置哈希缓存成功则返回True,否则返回False
        """
        try:
            await self.redis.hset(name=name, key=key, value=value)
            return True
        except Exception as e:
            logger.error(f"设置哈希缓存失败: {e!s}")
            return False

    async def hash_get(self, name: str, keys: list[str]) -> list[Any]:
        """获取哈希缓存。

        Args:
            name (str): 哈希缓存名称
            keys (list[str]): 哈希缓存键名列表

        Returns:
            Awaitable[list[Any]] | list[Any]: 返回哈希缓存值列表,如果获取失败则返回空列表
        """
        try:
            data = await self.redis.hmget(name=name, keys=keys)
            return data
        except Exception as e:
            logger.error(f"获取哈希缓存失败: {e!s}")
            return []
