"""员工档案 API — 提供入账编码所需的部门 / 成本中心 / 银行账号。

挂载在 /api/employees 前缀下：
  GET  /            列表
  GET  /me          当前用户的档案（任意角色可读）
  GET  /{id}        单条
  POST /            新建（finance_admin）
  PUT  /{id}        更新（finance_admin）
  DELETE /{id}      删除（finance_admin）
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext, require_auth, require_role
from backend.db.store import (
    delete_employee, get_db, get_employee, list_employees, upsert_employee,
)

router = APIRouter()


class EmployeeIn(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    department: str = "未分配"
    cost_center: str = "CC-00"
    manager_id: Optional[str] = None
    bank_account: Optional[str] = None
    level: Optional[str] = None
    hire_date: Optional[date] = None
    city: Optional[str] = "上海"


class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    department: Optional[str] = None
    cost_center: Optional[str] = None
    manager_id: Optional[str] = None
    bank_account: Optional[str] = None
    level: Optional[str] = None
    hire_date: Optional[date] = None
    city: Optional[str] = None


def _emp_dict(emp) -> dict:
    return {
        "id": emp.id,
        "name": emp.name,
        "email": emp.email,
        "department": emp.department,
        "cost_center": emp.cost_center,
        "manager_id": emp.manager_id,
        "bank_account": emp.bank_account,
        "level": emp.level,
        "hire_date": emp.hire_date.isoformat() if emp.hire_date else None,
        "city": emp.city,
        "created_at": emp.created_at.isoformat() if emp.created_at else None,
        "updated_at": emp.updated_at.isoformat() if emp.updated_at else None,
    }


@router.get("")
async def list_employees_route(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await list_employees(db, page=page, page_size=page_size)
    result["items"] = [_emp_dict(e) for e in result["items"]]
    return result


@router.get("/me")
async def get_my_employee(
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    emp = await get_employee(db, ctx.user_id)
    if not emp:
        raise HTTPException(status_code=404, detail="员工档案未建立")
    return _emp_dict(emp)


@router.get("/{employee_id}")
async def get_employee_route(
    employee_id: str,
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    emp = await get_employee(db, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    return _emp_dict(emp)


@router.post("", status_code=201)
async def create_employee(
    body: EmployeeIn,
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    existing = await get_employee(db, body.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"员工 {body.id} 已存在")
    emp = await upsert_employee(db, body.model_dump())
    return _emp_dict(emp)


@router.put("/{employee_id}")
async def update_employee(
    employee_id: str,
    body: EmployeeUpdate,
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    existing = await get_employee(db, employee_id)
    if not existing:
        raise HTTPException(status_code=404, detail="员工不存在")
    data = body.model_dump(exclude_none=True)
    data["id"] = employee_id
    emp = await upsert_employee(db, data)
    return _emp_dict(emp)


@router.delete("/{employee_id}", status_code=204)
async def delete_employee_route(
    employee_id: str,
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    ok = await delete_employee(db, employee_id)
    if not ok:
        raise HTTPException(status_code=404, detail="员工不存在")
    return None
