from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    declared_attr,
    mapped_column,
    relationship,
)

if TYPE_CHECKING:
    from app.api.v1.module_system.user.model import UserModel

from app.common.enums import PermissionFilterStrategy
from app.utils.common_util import uuid4_str


class MappedBase(AsyncAttrs, DeclarativeBase):
    """声明式基类。

    `AsyncAttrs <https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncAttrs>`__

    `DeclarativeBase <https://docs.sqlalchemy.org/en/20/orm/declarative_config.html>`__

    `mapped_column() <https://docs.sqlalchemy.org/en/20/orm/mapping_api.html#sqlalchemy.orm.mapped_column>`__

    MySQL 和 PostgreSQL 用于正式运行，SQLite 用于自动化测试
    """

    __abstract__: bool = True

    # 权限过滤策略，子类可以覆盖
    __permission_strategy__: PermissionFilterStrategy = PermissionFilterStrategy.DATA_SCOPE


class ModelMixin(MappedBase):
    """模型混入类 - 提供通用字段和功能。

    基础模型混合类 Mixin: 一种面向对象编程概念, 使结构变得更加清晰

    数据隔离设计原则：
    ==================
    数据权限 (created_id/updated_id):
        - 配合角色的data_scope字段实现精细化权限控制
        - 1:仅本人
        - 4:全部数据

    SQLAlchemy加载策略说明:
    - select(默认): 延迟加载,访问时单独查询
    - joined: 使用LEFT JOIN预加载
    - selectin: 使用IN查询批量预加载(推荐用于一对多)
    - subquery: 使用子查询预加载
    - raise/raise_on_sql: 禁止加载
    - noload: 不加载,返回None
    - immediate: 立即加载
    - write_only: 只写不读
    - dynamic: 返回查询对象,支持进一步过滤
    """

    __abstract__: bool = True

    # 基础字段
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="主键ID",
        index=True,
    )
    uuid: Mapped[str] = mapped_column(
        String(64),
        default=uuid4_str,
        nullable=False,
        unique=True,
        comment="UUID全局唯一标识",
        index=True,
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="是否已删除(0:未删除 1:已删除)",
        index=True,
    )
    created_time: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        nullable=False,
        comment="创建时间",
        index=True,
    )
    updated_time: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False,
        comment="更新时间",
        index=True,
    )
    deleted_time: Mapped[datetime | None] = mapped_column(
        DateTime,
        default=None,
        nullable=True,
        comment="删除时间",
        index=True,
    )


class UserMixin(MappedBase):
    """用户审计字段 Mixin。

    用于记录数据的创建者和更新者
    用于实现数据权限中的"仅本人数据权限"
    """

    __abstract__: bool = True

    created_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sys_user.id", ondelete="SET NULL", onupdate="CASCADE"),
        default=None,
        nullable=True,
        index=True,
        comment="创建人ID",
    )
    updated_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sys_user.id", ondelete="SET NULL", onupdate="CASCADE"),
        default=None,
        nullable=True,
        index=True,
        comment="更新人ID",
    )

    deleted_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sys_user.id", ondelete="SET NULL", onupdate="CASCADE"),
        default=None,
        nullable=True,
        index=True,
        comment="删除人ID",
    )

    @declared_attr
    def created_by(self) -> Mapped[Optional["UserModel"]]:
        """创建人关联关系（默认不加载，由 __loader_options__ 显式预加载）。

        Returns:
            Mapped[Optional[UserModel]]: 创建人 ORM 关系。
        """
        return relationship(
            "UserModel",
            lazy="noload",
            foreign_keys=lambda: self.created_id,  # pyright: ignore[reportArgumentType]
            uselist=False,
        )

    @declared_attr
    def updated_by(self) -> Mapped[Optional["UserModel"]]:
        """更新人关联关系（默认不加载，由 __loader_options__ 显式预加载）。

        Returns:
            Mapped[Optional[UserModel]]: 更新人 ORM 关系。
        """
        return relationship(
            "UserModel",
            lazy="noload",
            foreign_keys=lambda: self.updated_id,  # pyright: ignore[reportArgumentType]
            uselist=False,
        )

    @declared_attr
    def deleted_by(self) -> Mapped[Optional["UserModel"]]:
        """删除人关联关系（默认不加载，由 __loader_options__ 显式预加载）。

        Returns:
            Mapped[Optional[UserModel]]: 删除人 ORM 关系。
        """
        return relationship(
            "UserModel",
            lazy="noload",
            foreign_keys=lambda: self.deleted_id,  # pyright: ignore[reportArgumentType]
            uselist=False,
        )


# 延迟导入 UserModel 注册到 MappedBase.registry 中，供 UserMixin 的关联关系在独立或局部调用时正常解析
try:
    from app.api.v1.module_system.user.model import UserModel  # noqa: F401
except (ImportError, AttributeError):
    pass

