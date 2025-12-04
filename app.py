"""
預拌混凝土出車管理系統 v2 - FastAPI 應用程式

功能：
1. 基礎資料 CRUD（工程、車輛、配比、單價）
2. 快速出車匯入
3. 出車紀錄查詢
4. 報表統計
"""

from datetime import date, datetime
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Query, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
import pandas as pd
import io

from models import (
    init_db, get_db, SessionLocal, init_default_settings,
    Project, Mix, Truck, ProjectPrice, Dispatch, Setting, MaterialPrice
)
from calculator import DispatchCalculator


# ============================================================
# Pydantic Schemas
# ============================================================

# --- 材料單價 ---
class MaterialPriceCreate(BaseModel):
    price_id: str = Field(..., max_length=20)
    name: Optional[str] = None
    sand_price: float = 0.0
    stone_price: float = 0.0
    cement_price: float = 0.0
    slag_price: float = 0.0
    flyash_price: float = 0.0
    admixture_price: float = 0.0
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    note: Optional[str] = None

class MaterialPriceResponse(BaseModel):
    id: int
    price_id: str
    name: Optional[str]
    sand_price: float
    stone_price: float
    cement_price: float
    slag_price: float
    flyash_price: float
    admixture_price: float
    is_active: bool
    
    class Config:
        from_attributes = True

# --- 工程 ---
class ProjectCreate(BaseModel):
    code: str = Field(..., max_length=20)
    name: str = Field(..., max_length=100)
    address: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    default_distance_km: float = 10.0
    subsidy_threshold_m3: float = 6.0
    subsidy_amount: float = 500.0
    note: Optional[str] = None

class ProjectResponse(BaseModel):
    id: int
    code: str
    name: str
    default_distance_km: float
    subsidy_threshold_m3: float
    subsidy_amount: float
    is_active: bool
    
    class Config:
        from_attributes = True

# --- 車輛 ---
class TruckCreate(BaseModel):
    code: str
    plate_no: str
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    default_load_m3: float = 8.0
    fuel_l_per_km: float = 0.5
    driver_pay_per_trip: float = 800.0

class TruckResponse(BaseModel):
    id: int
    code: str
    plate_no: str
    driver_name: Optional[str]
    default_load_m3: float
    is_active: bool
    
    class Config:
        from_attributes = True

# --- 配比 ---
class MixCreate(BaseModel):
    code: str
    psi: int
    name: Optional[str] = None
    material_price_id: Optional[int] = None
    # 材料用量 (kg/m³)
    sand1_kg: float = 0.0
    sand2_kg: float = 0.0
    stone1_kg: float = 0.0
    stone2_kg: float = 0.0
    cement_kg: float = 0.0
    slag_kg: float = 0.0
    flyash_kg: float = 0.0
    admixture_kg: float = 0.0
    # 直接指定成本（如果沒設材料用量）
    material_cost_per_m3: float = 0.0

class MixResponse(BaseModel):
    id: int
    code: str
    psi: int
    name: Optional[str]
    material_price_id: Optional[int]
    sand1_kg: float
    sand2_kg: float
    stone1_kg: float
    stone2_kg: float
    cement_kg: float
    slag_kg: float
    flyash_kg: float
    admixture_kg: float
    material_cost_per_m3: float
    is_active: bool
    
    class Config:
        from_attributes = True

# --- 單價 ---
class PriceCreate(BaseModel):
    project_id: int
    mix_id: int
    price_per_m3: float
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None


# --- 系統設定 ---
class SettingResponse(BaseModel):
    key: str
    value: str


class SettingUpdate(BaseModel):
    value: str

# --- 出車 ---
class DispatchItem(BaseModel):
    """單車次資料"""
    truck: str = Field(..., description="車號/司機")
    load: float = Field(..., description="載量 m³")
    psi: Optional[str] = Field(None, description="強度，空白用預設")
    distance: Optional[float] = Field(None, description="距離，空白用案場預設")

class DispatchBatch(BaseModel):
    """批次出車"""
    date: str = Field(..., description="日期")
    project: str = Field(..., description="工程代碼或名稱")
    items: List[DispatchItem]

class DispatchResponse(BaseModel):
    id: int
    dispatch_no: str
    date: date
    project_code: str
    project_name: str
    truck_plate: str
    driver_name: Optional[str]
    mix_psi: int
    load_m3: float
    distance_km: float
    total_revenue: float
    total_cost: float
    gross_profit: float
    
    class Config:
        from_attributes = True


# ============================================================
# FastAPI App
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動時初始化"""
    init_db()
    db = SessionLocal()
    init_default_settings(db)
    db.close()
    yield

app = FastAPI(
    title="預拌混凝土出車管理系統 v2",
    description="簡化的出車管理、成本計算、損益分析",
    version="2.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 首頁
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    return get_main_page_html()

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """基礎資料管理介面"""
    return get_admin_page_html()


# ============================================================
# 工程 API
# ============================================================

@app.get("/api/projects", response_model=List[ProjectResponse])
def list_projects(
    active_only: bool = True,
    db: Session = Depends(get_db)
):
    """列出所有工程"""
    query = db.query(Project)
    if active_only:
        query = query.filter(Project.is_active == True)
    return query.order_by(Project.code).all()

@app.post("/api/projects", response_model=ProjectResponse)
def create_project(data: ProjectCreate, db: Session = Depends(get_db)):
    """新增工程"""
    existing = db.query(Project).filter(Project.code == data.code).first()
    if existing:
        raise HTTPException(400, f"工程代碼已存在：{data.code}")
    
    project = Project(**data.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project

@app.get("/api/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    """取得單一工程"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "工程不存在")
    return project

@app.put("/api/projects/{project_id}")
def update_project(project_id: int, data: ProjectCreate, db: Session = Depends(get_db)):
    """更新工程"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "工程不存在")
    
    for key, value in data.model_dump().items():
        setattr(project, key, value)

    db.commit()
    return {"status": "ok"}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """刪除工程"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "工程不存在")

    db.delete(project)
    db.commit()
    return {"status": "deleted"}


# ============================================================
# 車輛 API
# ============================================================

@app.get("/api/trucks", response_model=List[TruckResponse])
def list_trucks(active_only: bool = True, db: Session = Depends(get_db)):
    """列出所有車輛"""
    query = db.query(Truck)
    if active_only:
        query = query.filter(Truck.is_active == True)
    return query.order_by(Truck.code).all()

@app.post("/api/trucks", response_model=TruckResponse)
def create_truck(data: TruckCreate, db: Session = Depends(get_db)):
    """新增車輛"""
    existing = db.query(Truck).filter(Truck.code == data.code).first()
    if existing:
        raise HTTPException(400, f"車輛代碼已存在：{data.code}")
    
    truck = Truck(**data.model_dump())
    db.add(truck)
    db.commit()
    db.refresh(truck)
    return truck

@app.get("/api/trucks/{truck_id}")
def get_truck(truck_id: int, db: Session = Depends(get_db)):
    """取得單一車輛"""
    truck = db.query(Truck).filter(Truck.id == truck_id).first()
    if not truck:
        raise HTTPException(404, "車輛不存在")
    return {
        "id": truck.id,
        "code": truck.code,
        "plate_no": truck.plate_no,
        "driver_name": truck.driver_name,
        "driver_phone": truck.driver_phone,
        "default_load_m3": truck.default_load_m3,
        "fuel_l_per_km": truck.fuel_l_per_km,
        "driver_pay_per_trip": truck.driver_pay_per_trip,
        "is_active": truck.is_active
    }

@app.put("/api/trucks/{truck_id}")
def update_truck(truck_id: int, data: TruckCreate, db: Session = Depends(get_db)):
    """更新車輛"""
    truck = db.query(Truck).filter(Truck.id == truck_id).first()
    if not truck:
        raise HTTPException(404, "車輛不存在")
    
    for key, value in data.model_dump().items():
        if key != 'code':  # 不更新代號
            setattr(truck, key, value)

    db.commit()
    return {"status": "ok"}


@app.delete("/api/trucks/{truck_id}")
def delete_truck(truck_id: int, db: Session = Depends(get_db)):
    """刪除車輛"""
    truck = db.query(Truck).filter(Truck.id == truck_id).first()
    if not truck:
        raise HTTPException(404, "車輛不存在")

    db.delete(truck)
    db.commit()
    return {"status": "deleted"}


# ============================================================
# 材料單價 API
# ============================================================

@app.get("/api/material-prices", response_model=List[MaterialPriceResponse])
def list_material_prices(active_only: bool = True, db: Session = Depends(get_db)):
    """列出所有材料單價"""
    query = db.query(MaterialPrice)
    if active_only:
        query = query.filter(MaterialPrice.is_active == True)
    return query.order_by(MaterialPrice.price_id.desc()).all()

@app.post("/api/material-prices", response_model=MaterialPriceResponse)
def create_material_price(data: MaterialPriceCreate, db: Session = Depends(get_db)):
    """新增材料單價"""
    existing = db.query(MaterialPrice).filter(MaterialPrice.price_id == data.price_id).first()
    if existing:
        raise HTTPException(400, f"價格代碼已存在：{data.price_id}")
    
    mp = MaterialPrice(**data.model_dump())
    db.add(mp)
    db.commit()
    db.refresh(mp)
    return mp

@app.get("/api/material-prices/{mp_id}")
def get_material_price(mp_id: int, db: Session = Depends(get_db)):
    """取得單一材料單價"""
    mp = db.query(MaterialPrice).filter(MaterialPrice.id == mp_id).first()
    if not mp:
        raise HTTPException(404, "材料單價不存在")
    return {
        "id": mp.id,
        "price_id": mp.price_id,
        "name": mp.name,
        "sand_price": mp.sand_price,
        "stone_price": mp.stone_price,
        "cement_price": mp.cement_price,
        "slag_price": mp.slag_price,
        "flyash_price": mp.flyash_price,
        "admixture_price": mp.admixture_price,
        "is_active": mp.is_active
    }

@app.put("/api/material-prices/{mp_id}")
def update_material_price(mp_id: int, data: MaterialPriceCreate, db: Session = Depends(get_db)):
    """更新材料單價"""
    mp = db.query(MaterialPrice).filter(MaterialPrice.id == mp_id).first()
    if not mp:
        raise HTTPException(404, "材料單價不存在")
    
    for key, value in data.model_dump().items():
        if key != 'price_id':  # 不更新代碼
            setattr(mp, key, value)

    db.commit()
    return {"status": "ok"}


@app.delete("/api/material-prices/{mp_id}")
def delete_material_price(mp_id: int, db: Session = Depends(get_db)):
    """刪除材料單價"""
    mp = db.query(MaterialPrice).filter(MaterialPrice.id == mp_id).first()
    if not mp:
        raise HTTPException(404, "材料單價不存在")

    db.delete(mp)
    db.commit()
    return {"status": "deleted"}

@app.post("/api/material-prices/{mp_id}/recalc-mixes")
def recalc_mixes_cost(mp_id: int, db: Session = Depends(get_db)):
    """重新計算使用此材料單價的所有配比成本"""
    mp = db.query(MaterialPrice).filter(MaterialPrice.id == mp_id).first()
    if not mp:
        raise HTTPException(404, "材料單價不存在")
    
    mixes = db.query(Mix).filter(Mix.material_price_id == mp_id).all()
    updated = 0
    for mix in mixes:
        mix.material_cost_per_m3 = mix.calc_material_cost(mp)
        updated += 1
    
    db.commit()
    return {"status": "ok", "updated": updated}


# ============================================================
# 配比 API
# ============================================================

@app.get("/api/mixes", response_model=List[MixResponse])
def list_mixes(active_only: bool = True, db: Session = Depends(get_db)):
    """列出所有配比"""
    query = db.query(Mix)
    if active_only:
        query = query.filter(Mix.is_active == True)
    return query.order_by(Mix.psi).all()

@app.get("/api/mixes/{mix_id}")
def get_mix(mix_id: int, db: Session = Depends(get_db)):
    """取得單一配比詳情"""
    mix = db.query(Mix).filter(Mix.id == mix_id).first()
    if not mix:
        raise HTTPException(404, "配比不存在")
    
    result = {
        "id": mix.id,
        "code": mix.code,
        "psi": mix.psi,
        "name": mix.name,
        "material_price_id": mix.material_price_id,
        "sand1_kg": mix.sand1_kg,
        "sand2_kg": mix.sand2_kg,
        "stone1_kg": mix.stone1_kg,
        "stone2_kg": mix.stone2_kg,
        "cement_kg": mix.cement_kg,
        "slag_kg": mix.slag_kg,
        "flyash_kg": mix.flyash_kg,
        "admixture_kg": mix.admixture_kg,
        "material_cost_per_m3": mix.material_cost_per_m3,
        "is_active": mix.is_active
    }
    
    # 如果有材料單價，計算成本明細
    if mix.material_price:
        result["cost_breakdown"] = mix.get_material_breakdown()
        result["material_price_name"] = mix.material_price.name or mix.material_price.price_id
    
    return result

@app.post("/api/mixes", response_model=MixResponse)
def create_mix(data: MixCreate, db: Session = Depends(get_db)):
    """新增配比"""
    existing = db.query(Mix).filter(Mix.code == data.code).first()
    if existing:
        raise HTTPException(400, f"配比代碼已存在：{data.code}")
    
    mix = Mix(**data.model_dump())
    
    # 自動計算材料成本
    if mix.material_price_id:
        mp = db.query(MaterialPrice).filter(MaterialPrice.id == mix.material_price_id).first()
        if mp:
            mix.material_cost_per_m3 = mix.calc_material_cost(mp)
    
    db.add(mix)
    db.commit()
    db.refresh(mix)
    return mix

@app.put("/api/mixes/{mix_id}")
def update_mix(mix_id: int, data: MixCreate, db: Session = Depends(get_db)):
    """更新配比"""
    mix = db.query(Mix).filter(Mix.id == mix_id).first()
    if not mix:
        raise HTTPException(404, "配比不存在")
    
    for key, value in data.model_dump().items():
        if key != 'code':  # 不更新代號
            setattr(mix, key, value)
    
    # 重新計算材料成本
    if mix.material_price_id:
        mp = db.query(MaterialPrice).filter(MaterialPrice.id == mix.material_price_id).first()
        if mp:
            mix.material_cost_per_m3 = mix.calc_material_cost(mp)

    db.commit()
    return {"status": "ok", "material_cost_per_m3": mix.material_cost_per_m3}


@app.delete("/api/mixes/{mix_id}")
def delete_mix(mix_id: int, db: Session = Depends(get_db)):
    """刪除配比"""
    mix = db.query(Mix).filter(Mix.id == mix_id).first()
    if not mix:
        raise HTTPException(404, "配比不存在")

    db.delete(mix)
    db.commit()
    return {"status": "deleted"}


# ============================================================
# 單價 API
# ============================================================

@app.get("/api/prices")
def list_prices(
    project_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """列出單價"""
    query = db.query(ProjectPrice).filter(ProjectPrice.is_active == True)
    if project_id:
        query = query.filter(ProjectPrice.project_id == project_id)
    
    prices = query.all()
    return [{
        "id": p.id,
        "project_id": p.project_id,
        "mix_id": p.mix_id,
        "project_code": p.project.code,
        "project_name": p.project.name,
        "mix_code": p.mix.code,
        "mix_psi": p.mix.psi,
        "price_per_m3": p.price_per_m3,
        "effective_from": str(p.effective_from) if p.effective_from else None,
        "effective_to": str(p.effective_to) if p.effective_to else None,
        "is_active": p.is_active
    } for p in prices]

@app.post("/api/prices")
def create_price(data: PriceCreate, db: Session = Depends(get_db)):
    """新增/更新單價"""
    # 檢查是否已有相同的設定
    existing = db.query(ProjectPrice).filter(
        ProjectPrice.project_id == data.project_id,
        ProjectPrice.mix_id == data.mix_id,
        ProjectPrice.effective_from == data.effective_from,
        ProjectPrice.is_active == True
    ).first()
    
    if existing:
        existing.price_per_m3 = data.price_per_m3
        existing.effective_to = data.effective_to
    else:
        price = ProjectPrice(**data.model_dump())
        db.add(price)

    db.commit()
    return {"status": "ok"}


@app.delete("/api/prices/{price_id}")
def delete_price(price_id: int, db: Session = Depends(get_db)):
    """刪除工程單價"""
    price = db.query(ProjectPrice).filter(ProjectPrice.id == price_id).first()
    if not price:
        raise HTTPException(404, "單價不存在")

    db.delete(price)
    db.commit()
    return {"status": "deleted"}


# ============================================================
# 出車 API（核心功能）
# ============================================================

@app.post("/api/dispatch/preview")
def preview_dispatch(batch: DispatchBatch, db: Session = Depends(get_db)):
    """預覽批次出車"""
    calc = DispatchCalculator(db)
    results = []
    
    for idx, item in enumerate(batch.items):
        preview = calc.preview_dispatch(
            date_str=batch.date,
            project_str=batch.project,
            truck_str=item.truck,
            load_m3=item.load,
            mix_str=item.psi,
            distance_km=item.distance
        )
        preview["row_index"] = idx
        results.append(preview)
    
    return results

@app.post("/api/dispatch/commit")
def commit_dispatch(batch: DispatchBatch, db: Session = Depends(get_db)):
    """確認並寫入出車資料"""
    calc = DispatchCalculator(db)
    inserted = []
    errors = []
    
    for idx, item in enumerate(batch.items):
        try:
            dispatch = calc.create_dispatch(
                date_str=batch.date,
                project_str=batch.project,
                truck_str=item.truck,
                load_m3=item.load,
                mix_str=item.psi,
                distance_km=item.distance
            )
            inserted.append(dispatch.dispatch_no)
        except Exception as e:
            errors.append(f"第 {idx+1} 筆：{str(e)}")
    
    if inserted:
        db.commit()
    
    return {
        "success": len(errors) == 0,
        "inserted": len(inserted),
        "dispatch_nos": inserted,
        "errors": errors
    }

@app.get("/api/dispatches")
def list_dispatches(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    project_code: Optional[str] = None,
    limit: int = Query(100, le=1000),
    db: Session = Depends(get_db)
):
    """查詢出車紀錄"""
    query = db.query(Dispatch).filter(Dispatch.status != "cancelled")
    
    if start_date:
        query = query.filter(Dispatch.date >= start_date)
    if end_date:
        query = query.filter(Dispatch.date <= end_date)
    if project_code:
        project = db.query(Project).filter(Project.code == project_code).first()
        if project:
            query = query.filter(Dispatch.project_id == project.id)
    
    dispatches = query.order_by(Dispatch.date.desc(), Dispatch.dispatch_no).limit(limit).all()
    
    return [{
        "id": d.id,
        "dispatch_no": d.dispatch_no,
        "date": d.date.isoformat(),
        "project_code": d.project.code,
        "project_name": d.project.name,
        "truck_plate": d.truck.plate_no,
        "driver_name": d.truck.driver_name,
        "mix_psi": d.mix.psi,
        "load_m3": d.load_m3,
        "distance_km": d.distance_km,
        "price_per_m3": d.price_per_m3,
        "revenue": d.revenue,
        "subsidy": d.subsidy,
        "total_revenue": d.total_revenue,
        "material_cost": d.material_cost,
        "fuel_cost": d.fuel_cost,
        "driver_cost": d.driver_cost,
        "total_cost": d.total_cost,
        "gross_profit": d.gross_profit,
        "profit_margin": d.profit_margin,
    } for d in dispatches]


# ============================================================
# 報表 API
# ============================================================

@app.get("/api/reports/daily")
def report_daily(
    date_str: str,
    db: Session = Depends(get_db)
):
    """日報表"""
    dispatches = db.query(Dispatch).filter(
        Dispatch.date == date_str,
        Dispatch.status != "cancelled"
    ).all()
    
    summary = {
        "date": date_str,
        "total_trips": len(dispatches),
        "total_m3": sum(d.load_m3 for d in dispatches),
        "total_revenue": sum(d.total_revenue for d in dispatches),
        "total_cost": sum(d.total_cost for d in dispatches),
        "gross_profit": sum(d.gross_profit for d in dispatches),
    }
    
    by_project = {}
    for d in dispatches:
        key = d.project.code
        if key not in by_project:
            by_project[key] = {
                "project_name": d.project.name,
                "trips": 0, "m3": 0, "revenue": 0, "cost": 0, "profit": 0
            }
        by_project[key]["trips"] += 1
        by_project[key]["m3"] += d.load_m3
        by_project[key]["revenue"] += d.total_revenue
        by_project[key]["cost"] += d.total_cost
        by_project[key]["profit"] += d.gross_profit
    
    return {
        "summary": summary,
        "by_project": by_project
    }

@app.get("/api/reports/monthly")
def report_monthly(
    year: int,
    month: int,
    db: Session = Depends(get_db)
):
    """月報表"""
    dispatches = db.query(Dispatch).filter(
        extract('year', Dispatch.date) == year,
        extract('month', Dispatch.date) == month,
        Dispatch.status != "cancelled"
    ).all()
    
    summary = {
        "year": year,
        "month": month,
        "total_trips": len(dispatches),
        "total_m3": sum(d.load_m3 for d in dispatches),
        "total_revenue": sum(d.total_revenue for d in dispatches),
        "total_cost": sum(d.total_cost for d in dispatches),
        "gross_profit": sum(d.gross_profit for d in dispatches),
    }
    
    # 按工程統計
    by_project = {}
    for d in dispatches:
        key = d.project.code
        if key not in by_project:
            by_project[key] = {
                "project_name": d.project.name,
                "trips": 0, "m3": 0, "revenue": 0, "cost": 0, "profit": 0
            }
        by_project[key]["trips"] += 1
        by_project[key]["m3"] += d.load_m3
        by_project[key]["revenue"] += d.total_revenue
        by_project[key]["cost"] += d.total_cost
        by_project[key]["profit"] += d.gross_profit
    
    # 按日統計
    by_day = {}
    for d in dispatches:
        key = d.date.day
        if key not in by_day:
            by_day[key] = {"trips": 0, "m3": 0, "revenue": 0, "profit": 0}
        by_day[key]["trips"] += 1
        by_day[key]["m3"] += d.load_m3
        by_day[key]["revenue"] += d.total_revenue
        by_day[key]["profit"] += d.gross_profit
    
    return {
        "summary": summary,
        "by_project": by_project,
        "by_day": dict(sorted(by_day.items()))
    }

@app.get("/api/reports/project/{project_code}")
def report_project(
    project_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """工程報表"""
    project = db.query(Project).filter(Project.code == project_code).first()
    if not project:
        raise HTTPException(404, "工程不存在")
    
    query = db.query(Dispatch).filter(
        Dispatch.project_id == project.id,
        Dispatch.status != "cancelled"
    )
    
    if start_date:
        query = query.filter(Dispatch.date >= start_date)
    if end_date:
        query = query.filter(Dispatch.date <= end_date)
    
    dispatches = query.order_by(Dispatch.date).all()
    
    return {
        "project": {
            "code": project.code,
            "name": project.name,
            "default_distance_km": project.default_distance_km,
        },
        "summary": {
            "total_trips": len(dispatches),
            "total_m3": sum(d.load_m3 for d in dispatches),
            "total_revenue": sum(d.total_revenue for d in dispatches),
            "total_cost": sum(d.total_cost for d in dispatches),
            "gross_profit": sum(d.gross_profit for d in dispatches),
            "avg_profit_margin": sum(d.profit_margin for d in dispatches) / len(dispatches) if dispatches else 0,
        },
        "dispatches": [{
            "date": d.date.isoformat(),
            "dispatch_no": d.dispatch_no,
            "truck": d.truck.plate_no,
            "driver": d.truck.driver_name,
            "load_m3": d.load_m3,
            "revenue": d.total_revenue,
            "cost": d.total_cost,
            "profit": d.gross_profit,
        } for d in dispatches]
    }


# ============================================================
# 設定 API
# ============================================================

@app.get("/api/settings", response_model=List[SettingResponse])
def list_settings(db: Session = Depends(get_db)):
    """列出所有設定"""
    settings = db.query(Setting).all()
    return [SettingResponse(key=s.key, value=s.value) for s in settings]


@app.put("/api/settings/{key}")
def update_setting(key: str, data: SettingUpdate, db: Session = Depends(get_db)):
    """更新設定"""
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting:
        setting.value = data.value
    else:
        setting = Setting(key=key, value=data.value)
        db.add(setting)

    db.commit()
    return {"status": "ok", "key": key, "value": setting.value}


# ============================================================
# CSV 上傳
# ============================================================

@app.post("/api/dispatch/upload-csv")
async def upload_csv(
    file: UploadFile = File(...),
    default_date: Optional[str] = Form(None),
    default_project: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """上傳 CSV"""
    content = await file.read()
    
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(400, f"無法讀取 CSV：{e}")
    
    # 欄位對照
    col_map = {
        "工程": "project", "project_name": "project",
        "日期": "date", "車號": "truck", "司機": "truck",
        "載量": "load", "強度": "psi", "距離": "distance"
    }
    df.rename(columns=col_map, inplace=True)
    
    # 填入預設值
    if "date" not in df.columns and default_date:
        df["date"] = default_date
    if "project" not in df.columns and default_project:
        df["project"] = default_project
    
    # 檢查必要欄位
    required = ["date", "project", "truck", "load"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise HTTPException(400, f"缺少欄位：{missing}")
    
    # 預覽
    calc = DispatchCalculator(db)
    results = []
    
    for idx, row in df.iterrows():
        preview = calc.preview_dispatch(
            date_str=str(row["date"]),
            project_str=str(row["project"]),
            truck_str=str(row["truck"]),
            load_m3=float(row["load"]),
            mix_str=str(row.get("psi", "")) if pd.notna(row.get("psi")) else None,
            distance_km=float(row["distance"]) if pd.notna(row.get("distance")) else None
        )
        preview["row_index"] = idx
        results.append(preview)
    
    return {"previews": results, "total": len(df)}


# ============================================================
# HTML 頁面
# ============================================================

def get_main_page_html():
    return """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>預拌混凝土出車管理系統 v2</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { color: white; text-align: center; margin-bottom: 30px; text-shadow: 2px 2px 4px rgba(0,0,0,0.2); }
        
        .card {
            background: white;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
        }
        .card h2 { color: #333; margin-bottom: 20px; border-bottom: 2px solid #667eea; padding-bottom: 10px; }
        
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
        
        .form-row { display: flex; gap: 15px; margin-bottom: 15px; flex-wrap: wrap; }
        .form-group { flex: 1; min-width: 150px; }
        .form-group.wide { min-width: 300px; }
        label { display: block; margin-bottom: 5px; font-weight: 600; color: #555; }
        input, select { 
            width: 100%; padding: 12px; border: 2px solid #e0e0e0; border-radius: 8px;
            font-size: 14px; transition: border-color 0.3s;
        }
        input:focus, select:focus { outline: none; border-color: #667eea; }
        
        .btn {
            padding: 12px 24px; border: none; border-radius: 8px; cursor: pointer;
            font-size: 14px; font-weight: 600; transition: transform 0.2s, box-shadow 0.2s;
        }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.2); }
        .btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); color: white; }
        .btn-success { background: linear-gradient(135deg, #11998e, #38ef7d); color: white; }
        .btn-danger { background: #ff6b6b; color: white; }
        
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
        th { background: #f8f9fa; font-weight: 600; color: #555; }
        tr:hover { background: #f8f9fa; }
        
        .dispatch-input { width: 100%; border: none; padding: 8px; background: transparent; }
        .dispatch-input:focus { background: #fff3cd; outline: none; }
        
        .status-ok { color: #11998e; font-weight: 600; }
        .status-error { color: #ff6b6b; background: #ffe6e6; }
        
        .stat-card {
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white; padding: 20px; border-radius: 12px; text-align: center;
        }
        .stat-card h3 { font-size: 14px; opacity: 0.8; margin-bottom: 8px; }
        .stat-card .value { font-size: 28px; font-weight: 700; }
        
        .tabs { display: flex; gap: 5px; margin-bottom: 20px; }
        .tab {
            padding: 12px 24px; background: rgba(255,255,255,0.2); color: white;
            border: none; border-radius: 8px 8px 0 0; cursor: pointer; font-weight: 600;
        }
        .tab.active { background: white; color: #667eea; }
        
        #result-area { display: none; }
        .profit-positive { color: #11998e; }
        .profit-negative { color: #ff6b6b; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚛 預拌混凝土出車管理系統 v2</h1>
        <p style="text-align: center; margin-bottom: 20px;">
            <a href="/admin" style="color: white; text-decoration: none; background: rgba(255,255,255,0.2); padding: 8px 16px; border-radius: 20px;">⚙️ 基礎資料管理</a>
            <a href="/docs" target="_blank" style="color: white; text-decoration: none; background: rgba(255,255,255,0.2); padding: 8px 16px; border-radius: 20px; margin-left: 10px;">📖 API 文件</a>
        </p>
        
        <!-- 統計卡片 -->
        <div class="grid" id="stats-grid" style="margin-bottom: 20px;">
            <div class="stat-card">
                <h3>今日出車</h3>
                <div class="value" id="stat-trips">-</div>
            </div>
            <div class="stat-card">
                <h3>今日方數</h3>
                <div class="value" id="stat-m3">-</div>
            </div>
            <div class="stat-card">
                <h3>今日收入</h3>
                <div class="value" id="stat-revenue">-</div>
            </div>
            <div class="stat-card">
                <h3>今日毛利</h3>
                <div class="value" id="stat-profit">-</div>
            </div>
        </div>
        
        <!-- 主功能區 -->
        <div class="tabs">
            <button class="tab active" onclick="showTab('dispatch')">📥 快速出車</button>
            <button class="tab" onclick="showTab('records')">📋 出車紀錄</button>
            <button class="tab" onclick="showTab('master')">⚙️ 基礎資料</button>
        </div>
        
        <!-- 快速出車 -->
        <div id="tab-dispatch" class="card">
            <h2>📥 快速出車登錄</h2>
            <p style="color:#666; margin-bottom:20px;">選擇日期和工程後，只需輸入每車的「車號/司機」和「載量」</p>
            
            <div class="form-row">
                <div class="form-group">
                    <label>📅 日期</label>
                    <input type="date" id="dispatch-date">
                </div>
                <div class="form-group wide">
                    <label>🏗️ 工程</label>
                    <select id="dispatch-project"><option>載入中...</option></select>
                </div>
                <div class="form-group">
                    <label>預設強度</label>
                    <input type="text" id="default-psi" value="3000">
                </div>
            </div>
            
            <h3 style="margin: 20px 0 10px;">🚚 車次明細</h3>
            <table id="dispatch-table">
                <thead>
                    <tr>
                        <th style="width:40px">#</th>
                        <th>車號/司機</th>
                        <th style="width:100px">載量(m³)</th>
                        <th style="width:100px">強度</th>
                        <th style="width:100px">距離(km)</th>
                        <th style="width:60px">操作</th>
                    </tr>
                </thead>
                <tbody id="dispatch-body"></tbody>
            </table>
            
            <div style="margin-top:20px; display:flex; gap:10px;">
                <button class="btn btn-primary" onclick="addRow()">+ 新增一行</button>
                <button class="btn btn-primary" onclick="addRows(5)">+ 新增五行</button>
                <button class="btn btn-success" onclick="previewDispatch()">👁️ 預覽</button>
            </div>
        </div>
        
        <!-- 預覽結果 -->
        <div id="result-area" class="card">
            <h2>📊 預覽結果</h2>
            <div id="result-summary"></div>
            <table id="result-table">
                <thead id="result-thead"></thead>
                <tbody id="result-tbody"></tbody>
            </table>
            <button class="btn btn-success" onclick="commitDispatch()" style="margin-top:20px;">✅ 確認寫入</button>
        </div>
        
        <!-- 出車紀錄 -->
        <div id="tab-records" class="card" style="display:none;">
            <h2>📋 出車紀錄查詢</h2>
            <div class="form-row">
                <div class="form-group">
                    <label>起始日期</label>
                    <input type="date" id="query-start">
                </div>
                <div class="form-group">
                    <label>結束日期</label>
                    <input type="date" id="query-end">
                </div>
                <div class="form-group">
                    <label>工程</label>
                    <select id="query-project"><option value="">全部</option></select>
                </div>
                <div class="form-group" style="display:flex; align-items:flex-end;">
                    <button class="btn btn-primary" onclick="queryRecords()">🔍 查詢</button>
                </div>
            </div>
            <div id="records-result"></div>
        </div>
        
        <!-- 基礎資料 -->
        <div id="tab-master" class="card" style="display:none;">
            <h2>⚙️ 基礎資料管理</h2>
            <p>API 文件：<a href="/docs" target="_blank">/docs</a></p>
            <div class="grid" style="margin-top:20px;">
                <div>
                    <h3>工程 (<span id="project-count">0</span>)</h3>
                    <div id="project-list" style="max-height:300px; overflow:auto;"></div>
                </div>
                <div>
                    <h3>車輛 (<span id="truck-count">0</span>)</h3>
                    <div id="truck-list" style="max-height:300px; overflow:auto;"></div>
                </div>
                <div>
                    <h3>配比 (<span id="mix-count">0</span>)</h3>
                    <div id="mix-list" style="max-height:300px; overflow:auto;"></div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // 初始化
        const today = new Date().toISOString().split('T')[0];
        document.getElementById('dispatch-date').value = today;
        document.getElementById('query-start').value = today;
        document.getElementById('query-end').value = today;
        
        let currentBatch = null;
        let projects = [], trucks = [], mixes = [];
        
        // 載入資料
        async function loadData() {
            projects = await fetch('/api/projects').then(r => r.json());
            trucks = await fetch('/api/trucks').then(r => r.json());
            mixes = await fetch('/api/mixes').then(r => r.json());
            
            // 填充下拉選單
            const projectOptions = projects.map(p => `<option value="${p.code}">${p.name} (${p.code})</option>`).join('');
            document.getElementById('dispatch-project').innerHTML = '<option value="">請選擇</option>' + projectOptions;
            document.getElementById('query-project').innerHTML = '<option value="">全部</option>' + projectOptions;
            
            // 更新計數
            document.getElementById('project-count').textContent = projects.length;
            document.getElementById('truck-count').textContent = trucks.length;
            document.getElementById('mix-count').textContent = mixes.length;
            
            // 列出資料
            document.getElementById('project-list').innerHTML = projects.map(p => 
                `<div style="padding:8px; border-bottom:1px solid #eee;">${p.code} - ${p.name}</div>`
            ).join('');
            document.getElementById('truck-list').innerHTML = trucks.map(t => 
                `<div style="padding:8px; border-bottom:1px solid #eee;">${t.code} - ${t.plate_no} (${t.driver_name || '-'})</div>`
            ).join('');
            document.getElementById('mix-list').innerHTML = mixes.map(m => 
                `<div style="padding:8px; border-bottom:1px solid #eee;">${m.code} - ${m.psi}psi</div>`
            ).join('');
            
            // 載入今日統計
            loadTodayStats();
        }
        
        async function loadTodayStats() {
            try {
                const data = await fetch(`/api/reports/daily?date_str=${today}`).then(r => r.json());
                document.getElementById('stat-trips').textContent = data.summary.total_trips;
                document.getElementById('stat-m3').textContent = data.summary.total_m3.toFixed(1) + ' m³';
                document.getElementById('stat-revenue').textContent = '$' + data.summary.total_revenue.toLocaleString();
                document.getElementById('stat-profit').textContent = '$' + data.summary.gross_profit.toLocaleString();
            } catch(e) {
                console.log('No data for today');
            }
        }
        
        loadData();
        for(let i=0; i<3; i++) addRow();
        
        function showTab(name) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            document.querySelectorAll('[id^="tab-"]').forEach(el => el.style.display = 'none');
            document.getElementById('tab-' + name).style.display = 'block';
            document.getElementById('result-area').style.display = 'none';
        }
        
        function addRow() {
            const tbody = document.getElementById('dispatch-body');
            const n = tbody.children.length + 1;
            const psi = document.getElementById('default-psi').value;
            
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${n}</td>
                <td><input class="dispatch-input item-truck" placeholder="車號或司機"></td>
                <td><input class="dispatch-input item-load" type="number" step="0.5" placeholder="8"></td>
                <td><input class="dispatch-input item-psi" value="${psi}"></td>
                <td><input class="dispatch-input item-distance" type="number" step="0.1" placeholder="預設"></td>
                <td><button class="btn btn-danger" onclick="this.closest('tr').remove()" style="padding:5px 10px;">✕</button></td>
            `;
            tbody.appendChild(tr);
        }
        
        function addRows(n) { for(let i=0; i<n; i++) addRow(); }
        
        function collectItems() {
            const rows = document.querySelectorAll('#dispatch-body tr');
            const items = [];
            rows.forEach(tr => {
                const truck = tr.querySelector('.item-truck').value.trim();
                const load = tr.querySelector('.item-load').value;
                if (truck && load) {
                    items.push({
                        truck,
                        load: parseFloat(load),
                        psi: tr.querySelector('.item-psi').value || null,
                        distance: tr.querySelector('.item-distance').value ? parseFloat(tr.querySelector('.item-distance').value) : null
                    });
                }
            });
            return items;
        }
        
        async function previewDispatch() {
            const date = document.getElementById('dispatch-date').value;
            const project = document.getElementById('dispatch-project').value;
            const items = collectItems();
            
            if (!date || !project) { alert('請選擇日期和工程'); return; }
            if (!items.length) { alert('請輸入車次資料'); return; }
            
            currentBatch = { date, project, items };
            
            const res = await fetch('/api/dispatch/preview', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(currentBatch)
            });
            const data = await res.json();
            showResults(data);
        }
        
        function showResults(data) {
            const okCount = data.filter(d => d.status === 'OK').length;
            const errCount = data.length - okCount;
            const totalRevenue = data.filter(d => d.status === 'OK').reduce((s, d) => s + d.total_revenue, 0);
            const totalProfit = data.filter(d => d.status === 'OK').reduce((s, d) => s + d.gross_profit, 0);
            
            document.getElementById('result-summary').innerHTML = `
                <p>共 ${data.length} 筆：<span class="status-ok">✓ ${okCount} 成功</span>
                ${errCount ? `<span class="status-error"> ✕ ${errCount} 錯誤</span>` : ''}
                | 預估收入: $${totalRevenue.toLocaleString()} | 預估毛利: <span class="${totalProfit >= 0 ? 'profit-positive' : 'profit-negative'}">$${totalProfit.toLocaleString()}</span></p>
            `;
            
            document.getElementById('result-thead').innerHTML = `
                <tr><th>#</th><th>狀態</th><th>工程</th><th>車號</th><th>司機</th><th>載量</th><th>收入</th><th>成本</th><th>毛利</th><th>錯誤</th></tr>
            `;
            
            document.getElementById('result-tbody').innerHTML = data.map((d, i) => `
                <tr class="${d.status === 'ERROR' ? 'status-error' : ''}">
                    <td>${i+1}</td>
                    <td class="${d.status === 'OK' ? 'status-ok' : ''}">${d.status}</td>
                    <td>${d.project_name || '-'}</td>
                    <td>${d.truck_plate || '-'}</td>
                    <td>${d.driver_name || '-'}</td>
                    <td>${d.load_m3} m³</td>
                    <td>$${(d.total_revenue || 0).toLocaleString()}</td>
                    <td>$${(d.total_cost || 0).toLocaleString()}</td>
                    <td class="${(d.gross_profit || 0) >= 0 ? 'profit-positive' : 'profit-negative'}">$${(d.gross_profit || 0).toLocaleString()}</td>
                    <td>${d.error || ''}</td>
                </tr>
            `).join('');
            
            document.getElementById('result-area').style.display = 'block';
        }
        
        async function commitDispatch() {
            if (!currentBatch) return;
            if (!confirm(`確定寫入 ${currentBatch.items.length} 筆資料？`)) return;
            
            const res = await fetch('/api/dispatch/commit', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(currentBatch)
            });
            const data = await res.json();
            
            if (data.success) {
                alert(`✅ 成功寫入 ${data.inserted} 筆！\\n編號：${data.dispatch_nos.join(', ')}`);
                document.getElementById('dispatch-body').innerHTML = '';
                for(let i=0; i<3; i++) addRow();
                document.getElementById('result-area').style.display = 'none';
                loadTodayStats();
            } else {
                alert(`⚠️ 部分失敗：${data.inserted} 筆成功\\n\\n${data.errors.join('\\n')}`);
            }
        }
        
        async function queryRecords() {
            const start = document.getElementById('query-start').value;
            const end = document.getElementById('query-end').value;
            const project = document.getElementById('query-project').value;
            
            let url = `/api/dispatches?start_date=${start}&end_date=${end}`;
            if (project) url += `&project_code=${project}`;
            
            const data = await fetch(url).then(r => r.json());
            
            const total = {revenue: 0, cost: 0, profit: 0, m3: 0};
            data.forEach(d => {
                total.revenue += d.total_revenue;
                total.cost += d.total_cost;
                total.profit += d.gross_profit;
                total.m3 += d.load_m3;
            });
            
            document.getElementById('records-result').innerHTML = `
                <p style="margin:15px 0;">共 ${data.length} 筆 | ${total.m3.toFixed(1)} m³ | 收入 $${total.revenue.toLocaleString()} | 毛利 <span class="${total.profit >= 0 ? 'profit-positive' : 'profit-negative'}">$${total.profit.toLocaleString()}</span></p>
                <table>
                    <thead><tr><th>日期</th><th>編號</th><th>工程</th><th>車號</th><th>司機</th><th>載量</th><th>收入</th><th>成本</th><th>毛利</th></tr></thead>
                    <tbody>
                        ${data.map(d => `
                            <tr>
                                <td>${d.date}</td>
                                <td>${d.dispatch_no}</td>
                                <td>${d.project_name}</td>
                                <td>${d.truck_plate}</td>
                                <td>${d.driver_name || '-'}</td>
                                <td>${d.load_m3} m³</td>
                                <td>$${d.total_revenue.toLocaleString()}</td>
                                <td>$${d.total_cost.toLocaleString()}</td>
                                <td class="${d.gross_profit >= 0 ? 'profit-positive' : 'profit-negative'}">$${d.gross_profit.toLocaleString()}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        }
    </script>
</body>
</html>
"""


def get_admin_page_html():
    """管理介面 HTML - 讀取 admin.html 或使用內嵌備用"""
    import os
    # 嘗試讀取外部檔案
    admin_path = os.path.join(os.path.dirname(__file__), "admin.html")
    if os.path.exists(admin_path):
        with open(admin_path, "r", encoding="utf-8") as f:
            return f.read()
    
    # 備用：回傳簡易版本
    return """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <title>基礎資料管理</title>
    <style>
        body { font-family: sans-serif; padding: 20px; max-width: 1200px; margin: 0 auto; }
        h1 { color: #667eea; }
        .card { background: #f8f9fa; border-radius: 8px; padding: 20px; margin: 20px 0; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px; border-bottom: 1px solid #ddd; text-align: left; }
        .btn { padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; margin: 2px; }
        .btn-primary { background: #667eea; color: white; }
        .tabs { display: flex; gap: 10px; margin-bottom: 20px; }
        .tab { padding: 10px 20px; background: #e8ecef; border: none; cursor: pointer; border-radius: 4px; }
        .tab.active { background: #667eea; color: white; }
        .page { display: none; }
        .page.active { display: block; }
    </style>
</head>
<body>
    <h1>⚙️ 基礎資料管理</h1>
    <p><a href="/">← 返回出車系統</a> | <a href="/docs">API 文件</a></p>
    
    <div class="tabs">
        <button class="tab active" onclick="showPage('projects')">🏗️ 工程</button>
        <button class="tab" onclick="showPage('trucks')">🚛 車輛</button>
        <button class="tab" onclick="showPage('mixes')">🧱 配比</button>
        <button class="tab" onclick="showPage('settings')">⚙️ 設定</button>
    </div>
    
    <div id="page-projects" class="page active">
        <div class="card">
            <h2>工程列表</h2>
            <table><thead><tr><th>代號</th><th>名稱</th><th>預設距離</th></tr></thead>
            <tbody id="projects-table"></tbody></table>
        </div>
    </div>
    
    <div id="page-trucks" class="page">
        <div class="card">
            <h2>車輛列表</h2>
            <table><thead><tr><th>代號</th><th>車牌</th><th>司機</th></tr></thead>
            <tbody id="trucks-table"></tbody></table>
        </div>
    </div>
    
    <div id="page-mixes" class="page">
        <div class="card">
            <h2>配比列表</h2>
            <table><thead><tr><th>代號</th><th>PSI</th><th>成本/m³</th></tr></thead>
            <tbody id="mixes-table"></tbody></table>
        </div>
    </div>
    
    <div id="page-settings" class="page">
        <div class="card">
            <h2>系統設定</h2>
            <p>油價: <input type="number" id="fuel_price" step="0.1"> 元/L</p>
            <p>預設強度: <input type="number" id="default_psi"> PSI</p>
            <button class="btn btn-primary" onclick="saveSettings()">儲存</button>
        </div>
    </div>
    
    <script>
        function showPage(name) {
            document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById('page-' + name).classList.add('active');
            event.target.classList.add('active');
        }
        
        async function load() {
            const projects = await fetch('/api/projects').then(r => r.json());
            document.getElementById('projects-table').innerHTML = projects.map(p => 
                `<tr><td>${p.code}</td><td>${p.name}</td><td>${p.default_distance_km} km</td></tr>`
            ).join('');
            
            const trucks = await fetch('/api/trucks').then(r => r.json());
            document.getElementById('trucks-table').innerHTML = trucks.map(t => 
                `<tr><td>${t.code}</td><td>${t.plate_no}</td><td>${t.driver_name || '-'}</td></tr>`
            ).join('');
            
            const mixes = await fetch('/api/mixes').then(r => r.json());
            document.getElementById('mixes-table').innerHTML = mixes.map(m => 
                `<tr><td>${m.code}</td><td>${m.psi}</td><td>$${m.material_cost_per_m3}</td></tr>`
            ).join('');
            
            const settings = await fetch('/api/settings').then(r => r.json());
            settings.forEach(s => {
                const el = document.getElementById(s.key);
                if (el) el.value = s.value;
            });
        }
        
        async function saveSettings() {
            for (const key of ['fuel_price', 'default_psi']) {
                await fetch(`/api/settings/${key}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({value: document.getElementById(key).value})
                });
            }
            alert('已儲存');
        }
        
        load();
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
