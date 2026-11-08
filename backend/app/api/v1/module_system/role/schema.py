from dataclasses import dataclass
from typing import Literal

from fastapi import Query
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.api.v1.module_platform.menu.schema import MenuOutSchema
from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam, UserByQueryParam
from app.core.base_schema import BaseSchema, UserBySchema
from app.core.validator import validate_required_code


class RoleCreateSchema(BaseModel):
    """角色创建模型。"""

    name: str = Field(..., min_length=1, max_length=64, description="角色名称")
    code: str = Field(..., min_length=2, max_length=64, description="角色编码")
    order: int | None = Field(default=1, ge=0, description="显示排序")
    data_scope: Literal[1, 4] | None = Field(
        default=1,
        description="数据权限范围(1:仅本人 4:全部)",
    )
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启动 1:停用)")
    description: str | None = Field(default=None, max_length=255, description="描述")

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str):
        """校验角色编码：字母开头，2-64 位，仅含字母/数字/下划线。"""
        return validate_required_code(value)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: int):
        """校验状态：仅支持 0(正常)、1(禁用)。"""
        if value not in {0, 1}:
            raise ValueError("状态仅支持 0(正常) 或 1(禁用)")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str):
        """校验角色名称：不能为空。"""
        v = value.strip()
        if not v:
            raise ValueError("角色名称不能为空")
        return v


class RolePermissionSettingSchema(BaseModel):
    """角色权限配置模型。"""

    data_scope: Literal[1, 4] = Field(
        default=1,
        description="数据权限范围(1:仅本人 4:全部)",
    )
    role_ids: list[int] = Field(min_length=1, description="角色ID列表")
    menu_ids: list[int] = Field(default_factory=list, description="菜单ID列表")
class RoleUpdateSchema(RoleCreateSchema):
    """角色更新模型。"""


class RoleOutSchema(RoleCreateSchema, BaseSchema, UserBySchema):
    """角色信息响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    menus: list[MenuOutSchema] = Field(default_factory=list, description="角色菜单列表")


@dataclass
class RoleQueryParam(BaseQueryParam, UserByQueryParam):
    """角色管理查询参数。"""

    name: str | None = Query(None, description="角色名称")
    code: str | None = Query(None, description="角色编码")
    status: int | None = Query(None, description="状态(0:启动 1:停用)")

    def __post_init__(self) -> None:
        self.name = (QueueEnum.like.value, self.name)
        if self.code:
            self.code = (QueueEnum.like.value, self.code)
        if self.status is not None:
            self.status = (QueueEnum.eq.value, self.status)
