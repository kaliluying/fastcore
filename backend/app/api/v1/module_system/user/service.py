import io
from typing import Any

from fastapi import UploadFile
from openpyxl import load_workbook
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.module_platform.menu.crud import MenuCRUD
from app.api.v1.module_platform.menu.schema import MenuOutSchema, MenuTreeOutSchema
from app.config.setting import settings
from app.core.base_schema import AuthSchema, BatchSetAvailable
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import traversal_to_tree
from app.utils.excel_util import ExcelUtil
from app.utils.hash_bcrpy_util import PwdUtil
from app.utils.upload_util import UploadUtil

from .crud import UserCRUD
from .schema import (
    CurrentUserUpdateSchema,
    ResetPasswordSchema,
    UserChangePasswordSchema,
    UserCreateSchema,
    UserOutSchema,
    UserQueryParam,
    UserUpdateSchema,
)


class UserService:
    """用户管理服务。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    def _ensure_role_assignment_allowed(self) -> None:
        """仅允许超级管理员分配安全角色。

        Raises:
            CustomException: 当前用户不是超级管理员。
        """
        if not self.auth.user or not self.auth.user.is_superuser:
            raise CustomException(msg="仅超级管理员可以分配用户角色", code=10403, status_code=403)

    async def detail(self, id: int) -> UserOutSchema:
        user = await UserCRUD(self.auth).get_or_404(id=id)
        return UserOutSchema.model_validate(user)

    async def get_list(
        self,
        search: UserQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> list[UserOutSchema]:
        user_list = await UserCRUD(self.auth).get_list(search=vars(search) if search else None, order_by=order_by)
        return [UserOutSchema.model_validate(user) for user in user_list]

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: UserQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> dict:
        offset = (page_no - 1) * page_size
        return await UserCRUD(self.auth).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=vars(search) if search else None,
            out_schema=UserOutSchema,
        )

    async def create(self, data: UserCreateSchema) -> UserOutSchema:
        if not data.username:
            raise CustomException(msg="用户名不能为空")
        if data.is_superuser:
            raise CustomException(msg="不允许创建超级管理员")
        if data.role_ids:
            self._ensure_role_assignment_allowed()
        user = await UserCRUD(self.auth).get(username=data.username)
        if user:
            raise CustomException(msg="已存在相同用户名称的账号")

        if data.password:
            data.password = PwdUtil.hash_password(password=data.password)
        user_dict = data.model_dump(exclude_unset=True, exclude={"role_ids"})
        new_user = await UserCRUD(self.auth).create(data=user_dict)
        if data.role_ids and len(data.role_ids) > 0:
            await UserCRUD(self.auth).set_user_roles(user_ids=[new_user.id], role_ids=data.role_ids)
        loaded_user = await UserCRUD(self.auth).get(id=new_user.id)
        return UserOutSchema.model_validate(loaded_user or new_user)

    async def update(self, id: int, data: UserUpdateSchema) -> UserOutSchema:
        if not data.username:
            raise CustomException(msg="账号不能为空")

        user = await UserCRUD(self.auth).get_or_404(id=id)
        if user.is_superuser:
            raise CustomException(msg="超级管理员不允许修改")

        exist_user = await UserCRUD(self.auth).get(username=data.username)
        if exist_user and exist_user.id != id:
            raise CustomException(msg="更新失败，账号已存在")
        if data.mobile:
            exist_mobile_user = await UserCRUD(self.auth).get(mobile=data.mobile)
            if exist_mobile_user and exist_mobile_user.id != id:
                raise CustomException(msg="该数据已存在")
        if data.email:
            exist_email_user = await UserCRUD(self.auth).get(email=data.email)
            if exist_email_user and exist_email_user.id != id:
                raise CustomException(msg="该数据已存在")
        if "role_ids" in data.model_fields_set:
            self._ensure_role_assignment_allowed()
        new_user = await UserCRUD(self.auth).update(id=id, data=data)

        if "role_ids" in data.model_fields_set:
            await UserCRUD(self.auth).set_user_roles(user_ids=[id], role_ids=data.role_ids or [])

        return UserOutSchema.model_validate(new_user)

    async def delete(self, ids: list[int]) -> None:
        if len(ids) < 1:
            raise CustomException(msg="删除失败，删除对象不能为空")
        users = await UserCRUD(self.auth).get_list(search={"id": ("in", ids)})
        user_map = {u.id: u for u in users}
        for uid in ids:
            user = user_map.get(uid)
            if not user:
                raise CustomException(msg="该数据不存在")
            if user.is_superuser:
                raise CustomException(msg="超级管理员不能删除")
            if user.status == 0:
                raise CustomException(msg="用户已启用,不能删除")
            if self.auth.user and self.auth.user.id == uid:
                raise CustomException(msg="不能删除当前登陆用户")

        await UserCRUD(self.auth).set_user_roles(user_ids=ids, role_ids=[])
        await UserCRUD(self.auth).delete(ids=ids)

    async def current_info(self) -> UserOutSchema:
        if not self.auth.user or not self.auth.user.id:
            raise CustomException(msg="该数据不存在")
        user = await UserCRUD(self.auth).get(id=self.auth.user.id)
        user_dict = UserOutSchema.model_validate(user)
        if self.auth.user and self.auth.user.is_superuser:
            menu_all = await MenuCRUD(self.auth).tree_list(
                search={"type": ("in", [1, 2, 3, 4]), "status": 0},
                order_by=[{"order": "asc"}],
            )
            menus = [MenuOutSchema.model_validate(menu) for menu in menu_all]
        else:
            menu_ids = {
                menu.id
                for role in self.auth.user.roles or []
                if role.status == 0 and not getattr(role, "is_deleted", False)
                for menu in role.menus
                if menu.status == 0 and not getattr(menu, "is_deleted", False)
            }

            menus = (
                [
                    MenuOutSchema.model_validate(menu)
                    for menu in await MenuCRUD(self.auth).tree_list(
                        search={"id": ("in", list(menu_ids))},
                        order_by=[{"order": "asc"}],
                    )
                ]
                if menu_ids
                else []
            )
        from app.core.modules import get_modules, menu_is_active

        menus = [menu for menu in menus if menu_is_active(permission=menu.permission, route_name=menu.route_name)]
        user_dict.modules = [module.name for module in get_modules() if module.active]
        user_dict.permissions = self._collect_permissions()
        user_dict.menus = [
            MenuTreeOutSchema.model_validate(menu)
            for menu in traversal_to_tree([menu.model_dump() for menu in menus])
        ]
        return user_dict

    def _collect_permissions(self) -> list[str]:
        from app.core.modules import menu_is_active

        if self.auth.user and self.auth.user.is_superuser:
            return ["*:*:*"]

        permissions: set[str] = set()
        for role in self.auth.user.roles or []:
            if role.status != 0 or getattr(role, "is_deleted", False):
                continue
            for menu in role.menus or []:
                if (
                    menu.status == 0
                    and not getattr(menu, "is_deleted", False)
                    and menu.permission
                    and menu_is_active(permission=menu.permission, route_name=menu.route_name)
                ):
                    permissions.add(menu.permission)
        return sorted(permissions)

    async def update_current_info(self, data: CurrentUserUpdateSchema) -> UserOutSchema:
        if not self.auth.user or not self.auth.user.id:
            raise CustomException(msg="该数据不存在")
        user = await UserCRUD(self.auth).get(id=self.auth.user.id)
        if not user:
            raise CustomException(msg="该数据不存在")
        if data.mobile:
            exist_mobile_user = await UserCRUD(self.auth).get(mobile=data.mobile)
            if exist_mobile_user and exist_mobile_user.id != self.auth.user.id:
                raise CustomException(msg="该数据已存在")
        if data.email:
            exist_email_user = await UserCRUD(self.auth).get(email=data.email)
            if exist_email_user and exist_email_user.id != self.auth.user.id:
                raise CustomException(msg="该数据已存在")
        user_update_data = UserUpdateSchema(**data.model_dump(exclude_unset=True))
        new_user = await UserCRUD(self.auth).update(id=self.auth.user.id, data=user_update_data)
        return UserOutSchema.model_validate(new_user)

    async def set_available(self, data: BatchSetAvailable) -> None:
        crud = UserCRUD(self.auth)
        users = await crud.get_list(search={"id": ("in", data.ids)})
        user_map = {user.id: user for user in users}
        for mid in data.ids:
            user = user_map.get(mid)
            if not user:
                raise CustomException(msg="该数据不存在")
            if user.is_superuser:
                raise CustomException(msg="超级管理员状态不能修改")
        await crud.set(ids=data.ids, status=data.status)

    async def change_password(self, data: UserChangePasswordSchema) -> UserOutSchema:
        if not self.auth.user or not self.auth.user.id:
            raise CustomException(msg="该数据不存在")
        if not data.old_password or not data.new_password:
            raise CustomException(msg="密码不能为空")

        user = await UserCRUD(self.auth).get(id=self.auth.user.id)
        if not user:
            raise CustomException(msg="该数据不存在")
        if not PwdUtil.verify_password(plain_password=data.old_password, password_hash=user.password):
            raise CustomException(msg="原密码输入错误")

        new_password_hash = PwdUtil.hash_password(password=data.new_password)
        new_user = await UserCRUD(self.auth).change_password(id=user.id, password_hash=new_password_hash)
        return UserOutSchema.model_validate(new_user)

    async def reset_password(self, data: ResetPasswordSchema) -> UserOutSchema:
        if not data.password:
            raise CustomException(msg="密码不能为空")

        user = await UserCRUD(self.auth).get(id=data.id)
        if not user:
            raise CustomException(msg="该数据不存在")

        if user.is_superuser:
            raise CustomException(msg="超级管理员密码不能重置")

        new_password_hash = PwdUtil.hash_password(password=data.password)
        new_user = await UserCRUD(self.auth).change_password(id=data.id, password_hash=new_password_hash)
        return UserOutSchema.model_validate(new_user)

    async def batch_import(self, file: UploadFile, update_support: bool = False) -> dict[str, Any]:
        header_dict = {
            "账号": "username",
            "昵称": "name",
            "邮箱": "email",
            "手机号": "mobile",
            "性别": "gender",
            "状态": "status",
        }

        try:
            UploadUtil.check_file_size(file)
            contents = await file.read()
            if len(contents) > settings.MAX_FILE_SIZE:
                raise CustomException(
                    msg=f"文件大小超过限制，最大允许 {settings.MAX_FILE_SIZE // (1024 * 1024)}MB"
                )
            await file.close()

            try:
                workbook = load_workbook(io.BytesIO(contents), data_only=True)
            except Exception as read_err:
                raise CustomException(msg="导入文件解析失败，请上传有效的 Excel 文件") from read_err

            worksheet = workbook.active
            if worksheet is None:
                raise CustomException(msg="导入文件为空")

            rows_iter = worksheet.iter_rows(values_only=True)
            header_row = next(rows_iter, None)
            if not header_row or all(c is None for c in header_row):
                raise CustomException(msg="导入文件为空")

            headers = [str(c).strip() if c is not None else "" for c in header_row]
            missing_headers = [header for header in header_dict if header not in headers]
            if missing_headers:
                raise CustomException(msg=f"导入文件缺少必要的列: {', '.join(missing_headers)}")

            col_indices = {header_dict[h]: headers.index(h) for h in header_dict}

            # 遍历数据行，过滤全空行
            data_rows: list[tuple[int, tuple[Any, ...]]] = []
            for row_idx, row in enumerate(rows_iter, start=2):
                if any(c is not None and str(c).strip() != "" for c in row):
                    data_rows.append((row_idx, row))

            if not data_rows:
                raise CustomException(msg="导入文件为空")

            success_count = 0
            errors = []
            gender_codes = {"男": "0", "女": "1", "未知": "2", "0": "0", "1": "1", "2": "2"}
            default_password_hash = PwdUtil.hash_password(password="123456")

            def _get_val(current_row: tuple[Any, ...], field_name: str) -> Any:
                idx = col_indices[field_name]
                return current_row[idx] if idx < len(current_row) else None

            for row_number, row in data_rows:
                try:
                    raw_user = _get_val(row, "username")
                    raw_name = _get_val(row, "name")
                    username = str(raw_user).strip() if raw_user is not None and str(raw_user).strip() not in ("", "None") else ""
                    name = str(raw_name).strip() if raw_name is not None and str(raw_name).strip() not in ("", "None") else ""
                    if not username:
                        raise CustomException(msg="账号不能为空")
                    if not name:
                        raise CustomException(msg="昵称不能为空")

                    raw_gender = _get_val(row, "gender")
                    gender = str(raw_gender).strip() if raw_gender is not None else "未知"
                    if isinstance(raw_gender, (int, float)) and raw_gender in (0, 1, 2):
                        gender = str(int(raw_gender))
                    elif raw_gender is None or gender == "" or gender.lower() == "none":
                        gender = "未知"
                    if gender not in gender_codes:
                        raise CustomException(msg="性别仅支持男、女、未知或编码 0、1、2")

                    raw_email = _get_val(row, "email")
                    raw_mobile = _get_val(row, "mobile")
                    raw_status = _get_val(row, "status")

                    email = str(raw_email).strip() if raw_email is not None and str(raw_email).strip() not in ("", "None") else None
                    if isinstance(raw_mobile, float) and raw_mobile.is_integer():
                        mobile = str(int(raw_mobile))
                    else:
                        mobile = str(raw_mobile).strip() if raw_mobile is not None and str(raw_mobile).strip() not in ("", "None") else None

                    status_str = str(raw_status).strip() if raw_status is not None else ""
                    status = 0 if status_str == "正常" else 1

                    user_data = {
                        "username": username,
                        "name": name,
                        "email": email,
                        "mobile": mobile,
                        "gender": gender_codes[gender],
                        "status": status,
                    }

                    async with self.auth.db.begin_nested():
                        exists_user = await UserCRUD(self.auth).get(username=user_data["username"])
                        if exists_user:
                            if exists_user.is_superuser:
                                raise CustomException(msg="超级管理员不允许修改")
                            if not update_support:
                                raise CustomException(msg=f"用户 {user_data['username']} 已存在")
                            user_update_data = UserUpdateSchema(**user_data)
                            await UserCRUD(self.auth).update(id=exists_user.id, data=user_update_data)
                        else:
                            new_user_data = {
                                **user_data,
                                "password": default_password_hash,
                            }
                            user_create_schema = UserCreateSchema(**new_user_data)
                            user_create_data = user_create_schema.model_dump(exclude_unset=True, exclude={"role_ids"})
                            new_user = await UserCRUD(self.auth).create(data=user_create_data)
                            if user_create_schema.role_ids and len(user_create_schema.role_ids) > 0:
                                await UserCRUD(self.auth).set_user_roles(user_ids=[new_user.id], role_ids=user_create_schema.role_ids)
                    success_count += 1
                except ValidationError as error:
                    message = "；".join(f"{'.'.join(map(str, detail['loc']))}: {detail['msg']}" for detail in error.errors(include_input=False))
                    errors.append({"row": row_number, "message": message})
                except CustomException as error:
                    message = "数据写入失败，请检查账号、邮箱或手机号是否重复" if isinstance(error.__context__, SQLAlchemyError) else error.msg
                    errors.append({"row": row_number, "message": message})
                except Exception:
                    errors.append({"row": row_number, "message": "数据写入失败，请检查数据格式或唯一字段"})

            message = f"成功导入 {success_count} 条数据"
            if errors:
                message += "\n错误信息:\n" + "\n".join(f"第{error['row']}行: {error['message']}" for error in errors)
            return {"success_count": success_count, "failed_count": len(errors), "errors": errors, "message": message}

        except Exception as e:
            logger.error(f"批量导入用户失败: {e!s}")
            raise CustomException(msg=f"导入失败: {e!s}") from e

    @staticmethod
    def get_import_template() -> bytes:
        header_list = [
            "账号",
            "昵称",
            "邮箱",
            "手机号",
            "性别",
            "状态",
        ]
        selector_header_list = ["性别", "状态"]
        option_list = [
            {"性别": ["男", "女", "未知"]},
            {"状态": ["正常", "停用"]},
        ]
        return ExcelUtil.get_excel_template(
            header_list=header_list,
            selector_header_list=selector_header_list,
            option_list=option_list,
        )

    @staticmethod
    def export_list(user_list: list[UserOutSchema | dict[str, Any]]) -> bytes:
        if not user_list:
            raise CustomException(msg="没有数据可导出")

        mapping_dict = {
            "id": "用户编号",
            "avatar": "头像",
            "username": "用户名称",
            "name": "用户昵称",
            "email": "邮箱",
            "mobile": "手机号",
            "gender": "性别",
            "status": "状态",
            "is_superuser": "是否超级管理员",
            "last_login": "最后登录时间",
            "description": "备注",
            "created_time": "创建时间",
            "updated_time": "更新时间",
            "updated_id": "更新者ID",
        }

        data = [item.model_dump() if isinstance(item, UserOutSchema) else item.copy() for item in user_list]
        for item in data:
            item["status"] = "启用" if item.get("status") == 0 else "停用"
            gender = item.get("gender")
            item["gender"] = {"0": "男", "1": "女", "2": "未知"}.get(str(gender), "未知")
            item["is_superuser"] = "是" if item.get("is_superuser") else "否"
            item["creator"] = item.get("created_by", {}).get("name", "未知") if isinstance(item.get("created_by"), dict) else "未知"

        return ExcelUtil.export_list2excel(list_data=data, mapping_dict=mapping_dict)
