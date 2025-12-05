diff --git a/app.py b/app.py
index d0029ad9aa5b2204fa0bf6d1713b343154c56f01..e831f83d5740b7e80826e0ad77c92319e4367275 100644
--- a/app.py
+++ b/app.py
@@ -1,51 +1,53 @@
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
+from sqlalchemy.exc import SQLAlchemyError
 import pandas as pd
 import io
 
 from models import (
     init_db, get_db, SessionLocal, init_default_settings,
-    Project, Mix, Truck, ProjectPrice, Dispatch, Setting, MaterialPrice
+    Project, Mix, Truck, ProjectPrice, Dispatch, Setting, MaterialPrice,
+    DailySummary
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
@@ -78,51 +80,54 @@ class ProjectResponse(BaseModel):
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
+    driver_phone: Optional[str]
     default_load_m3: float
+    fuel_l_per_km: float
+    driver_pay_per_trip: float
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
@@ -130,176 +135,265 @@ class MixResponse(BaseModel):
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
 
+
+# --- 系統設定 ---
+class SettingResponse(BaseModel):
+    key: str
+    value: str
+
+
+class SettingUpdate(BaseModel):
+    value: str
+
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
 
 
+class DailySummaryCreate(BaseModel):
+    date: date
+    project: str
+    mix: Optional[str] = None
+    psi: Optional[int] = None
+    total_m3: float
+    trips: int = 0
+    driver_count: Optional[int] = None
+    driver_daily_pay: Optional[float] = None
+
+
+class DailySummaryResponse(BaseModel):
+    id: int
+    date: date
+    project_code: str
+    project_name: str
+    mix_id: Optional[int]
+    mix_code: Optional[str]
+    psi: Optional[int]
+    total_m3: float
+    trips: int
+    driver_count: Optional[int]
+    driver_daily_pay: Optional[float]
+
+
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
 
 
+def get_project_by_code_or_name(db: Session, query: str) -> Project:
+    """用代碼或名稱尋找工程（精確匹配）。"""
+    project = db.query(Project).filter(
+        (Project.code == query) | (Project.name == query)
+    ).first()
+    if not project:
+        raise HTTPException(404, f"找不到工程：{query}")
+    return project
+
+
+def get_mix_by_code_or_psi(db: Session, query: str) -> Mix:
+    """用代碼或強度尋找配比。"""
+    if not query:
+        raise HTTPException(400, "配比不可為空")
+
+    mix = db.query(Mix).filter((Mix.code == query) | (Mix.psi == try_parse_int(query))).first()
+    if not mix:
+        raise HTTPException(404, f"找不到配比：{query}")
+    return mix
+
+
+def try_parse_int(raw: Optional[str]) -> Optional[int]:
+    try:
+        return int(raw)
+    except Exception:
+        return None
+
+
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
-    
+
     db.commit()
     return {"status": "ok"}
 
 
+@app.delete("/api/projects/{project_id}")
+def delete_project(project_id: int, db: Session = Depends(get_db)):
+    """刪除工程"""
+    project = db.query(Project).filter(Project.id == project_id).first()
+    if not project:
+        raise HTTPException(404, "工程不存在")
+
+    has_dispatch = db.query(Dispatch).filter(Dispatch.project_id == project_id).first()
+    has_price = db.query(ProjectPrice).filter(ProjectPrice.project_id == project_id).first()
+
+    if has_dispatch or has_price:
+        project.is_active = False
+        db.commit()
+        return {"status": "disabled", "message": "已有出車或單價紀錄，改為停用"}
+
+    try:
+        db.delete(project)
+        db.commit()
+        return {"status": "deleted", "message": "已刪除工程"}
+    except SQLAlchemyError:
+        db.rollback()
+        project.is_active = False
+        db.commit()
+        return {"status": "disabled", "message": "刪除失敗，已改為停用"}
+
+
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
 
@@ -309,55 +403,80 @@ def get_truck(truck_id: int, db: Session = Depends(get_db)):
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
-    
+
     db.commit()
     return {"status": "ok"}
 
 
+@app.delete("/api/trucks/{truck_id}")
+def delete_truck(truck_id: int, db: Session = Depends(get_db)):
+    """刪除車輛"""
+    truck = db.query(Truck).filter(Truck.id == truck_id).first()
+    if not truck:
+        raise HTTPException(404, "車輛不存在")
+
+    has_dispatch = db.query(Dispatch).filter(Dispatch.truck_id == truck_id).first()
+
+    if has_dispatch:
+        truck.is_active = False
+        db.commit()
+        return {"status": "disabled", "message": "已有出車紀錄，改為停用"}
+
+    try:
+        db.delete(truck)
+        db.commit()
+        return {"status": "deleted", "message": "已刪除車輛"}
+    except SQLAlchemyError:
+        db.rollback()
+        truck.is_active = False
+        db.commit()
+        return {"status": "disabled", "message": "刪除失敗，已改為停用"}
+
+
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
 
@@ -368,54 +487,79 @@ def get_material_price(mp_id: int, db: Session = Depends(get_db)):
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
-    
+
     db.commit()
     return {"status": "ok"}
 
+
+@app.delete("/api/material-prices/{mp_id}")
+def delete_material_price(mp_id: int, db: Session = Depends(get_db)):
+    """刪除材料單價"""
+    mp = db.query(MaterialPrice).filter(MaterialPrice.id == mp_id).first()
+    if not mp:
+        raise HTTPException(404, "材料單價不存在")
+
+    has_mix = db.query(Mix).filter(Mix.material_price_id == mp_id).first()
+
+    if has_mix:
+        mp.is_active = False
+        db.commit()
+        return {"status": "disabled", "message": "已有配比使用，改為停用"}
+
+    try:
+        db.delete(mp)
+        db.commit()
+        return {"status": "deleted", "message": "已刪除材料單價"}
+    except SQLAlchemyError:
+        db.rollback()
+        mp.is_active = False
+        db.commit()
+        return {"status": "disabled", "message": "刪除失敗，已改為停用"}
+
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
@@ -469,106 +613,151 @@ def create_mix(data: MixCreate, db: Session = Depends(get_db)):
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
-    
+
     db.commit()
     return {"status": "ok", "material_cost_per_m3": mix.material_cost_per_m3}
 
 
+@app.delete("/api/mixes/{mix_id}")
+def delete_mix(mix_id: int, db: Session = Depends(get_db)):
+    """刪除配比"""
+    mix = db.query(Mix).filter(Mix.id == mix_id).first()
+    if not mix:
+        raise HTTPException(404, "配比不存在")
+
+    has_dispatch = db.query(Dispatch).filter(Dispatch.mix_id == mix_id).first()
+    has_price = db.query(ProjectPrice).filter(ProjectPrice.mix_id == mix_id).first()
+    referenced_by_project = db.query(Project).filter(Project.default_mix_id == mix_id).first()
+
+    if has_dispatch or has_price or referenced_by_project:
+        mix.is_active = False
+        db.commit()
+        return {"status": "disabled", "message": "已有出車、單價或工程引用，改為停用"}
+
+    try:
+        db.delete(mix)
+        db.commit()
+        return {"status": "deleted", "message": "已刪除配比"}
+    except SQLAlchemyError:
+        db.rollback()
+        mix.is_active = False
+        db.commit()
+        return {"status": "disabled", "message": "刪除失敗，已改為停用"}
+
+
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
-    
+
     db.commit()
     return {"status": "ok"}
 
 
+@app.delete("/api/prices/{price_id}")
+def delete_price(price_id: int, db: Session = Depends(get_db)):
+    """刪除工程單價"""
+    price = db.query(ProjectPrice).filter(ProjectPrice.id == price_id).first()
+    if not price:
+        raise HTTPException(404, "單價不存在")
+
+    try:
+        db.delete(price)
+        db.commit()
+        return {"status": "deleted", "message": "已刪除工程單價"}
+    except SQLAlchemyError:
+        db.rollback()
+        price.is_active = False
+        db.commit()
+        return {"status": "disabled", "message": "刪除失敗，已改為停用"}
+
+
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
@@ -580,267 +769,483 @@ def commit_dispatch(batch: DispatchBatch, db: Session = Depends(get_db)):
     
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
 
+
+def get_avg_fuel_consumption(db: Session) -> float:
+    """取得平均油耗，若沒有車輛則回傳預設 0.5 L/km。"""
+    values = [t.fuel_l_per_km for t in db.query(Truck).filter(Truck.is_active == True).all() if t.fuel_l_per_km]
+    if not values:
+        return 0.5
+    return sum(values) / len(values)
+
+
+def build_range_summary(
+    db: Session,
+    start_date: date,
+    end_date: date,
+    driver_count: Optional[int] = None,
+    driver_daily_pay: Optional[float] = None,
+):
+    """計算自選日期區間的收入/成本/毛利。"""
+    calc = DispatchCalculator(db)
+    fuel_price = calc.get_fuel_price()
+    avg_fuel = get_avg_fuel_consumption(db)
+
+    dispatches = db.query(Dispatch).filter(
+        Dispatch.date >= start_date,
+        Dispatch.date <= end_date,
+        Dispatch.status != "cancelled",
+    ).all()
+
+    summaries = db.query(DailySummary).join(Project).filter(
+        DailySummary.date >= start_date,
+        DailySummary.date <= end_date,
+    ).all()
+
+    by_project = {}
+
+    def ensure_project(key: str, name: str):
+        if key not in by_project:
+            by_project[key] = {
+                "project_name": name,
+                "trips": 0,
+                "m3": 0.0,
+                "revenue": 0.0,
+                "base_cost": 0.0,
+            }
+
+    # 1) 出車紀錄（含收入、材料與油料成本）
+    for d in dispatches:
+        key = d.project.code
+        ensure_project(key, d.project.name)
+        by_project[key]["trips"] += 1
+        by_project[key]["m3"] += d.load_m3
+        by_project[key]["revenue"] += d.total_revenue
+        # 移除原本的司機成本，改在後面統一分攤
+        by_project[key]["base_cost"] += (d.total_cost - d.driver_cost)
+
+    # 2) 日彙總紀錄（用配比與價格估算收入與成本）
+    for s in summaries:
+        key = s.project.code
+        ensure_project(key, s.project.name)
+
+        mix = s.mix or (s.psi and db.query(Mix).filter(Mix.psi == s.psi).first())
+        if not mix:
+            raise HTTPException(400, f"找不到配比：工程 {s.project.code} {s.date}")
+
+        try:
+            price_per_m3 = calc.get_price(s.project, mix, s.date)
+        except Exception:
+            price_per_m3 = 0.0
+
+        by_project[key]["trips"] += s.trips
+        by_project[key]["m3"] += s.total_m3
+        by_project[key]["revenue"] += s.total_m3 * price_per_m3
+
+        material_cost = s.total_m3 * (mix.material_cost_per_m3 or 0)
+        fuel_cost = (s.project.default_distance_km or 0) * 2 * avg_fuel * fuel_price * (s.trips or 0)
+        by_project[key]["base_cost"] += material_cost + fuel_cost
+
+    total_trips = sum(p["trips"] for p in by_project.values())
+    driver_total = (driver_count or 0) * (driver_daily_pay or 0)
+    driver_per_trip = driver_total / total_trips if total_trips else 0
+
+    summary = {
+        "start_date": start_date,
+        "end_date": end_date,
+        "total_trips": total_trips,
+        "total_m3": sum(p["m3"] for p in by_project.values()),
+        "total_revenue": sum(p["revenue"] for p in by_project.values()),
+        "driver_cost": driver_total,
+        "driver_cost_per_trip": driver_per_trip,
+        "total_cost": 0.0,
+        "gross_profit": 0.0,
+    }
+
+    for p in by_project.values():
+        p["driver_cost"] = driver_per_trip * p["trips"]
+        p["total_cost"] = p["base_cost"] + p["driver_cost"]
+        p["gross_profit"] = p["revenue"] - p["total_cost"]
+
+    summary["total_cost"] = sum(p["total_cost"] for p in by_project.values())
+    summary["gross_profit"] = summary["total_revenue"] - summary["total_cost"]
+
+    return {
+        "summary": summary,
+        "by_project": by_project,
+    }
+
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
-    
+
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
 
 
+# ============================================================
+# 日彙總 API
+# ============================================================
+
+@app.get("/api/daily-summaries", response_model=List[DailySummaryResponse])
+def list_daily_summaries(
+    start_date: Optional[date] = None,
+    end_date: Optional[date] = None,
+    project_code: Optional[str] = None,
+    db: Session = Depends(get_db)
+):
+    query = db.query(DailySummary).join(Project)
+    if start_date:
+        query = query.filter(DailySummary.date >= start_date)
+    if end_date:
+        query = query.filter(DailySummary.date <= end_date)
+    if project_code:
+        query = query.filter(Project.code == project_code)
+
+    summaries = query.order_by(DailySummary.date.desc()).all()
+    results = []
+    for s in summaries:
+        results.append({
+            "id": s.id,
+            "date": s.date,
+            "project_code": s.project.code,
+            "project_name": s.project.name,
+            "mix_id": s.mix_id,
+            "mix_code": s.mix.code if s.mix else None,
+            "psi": s.psi,
+            "total_m3": s.total_m3,
+            "trips": s.trips,
+            "driver_count": s.driver_count,
+            "driver_daily_pay": s.driver_daily_pay,
+        })
+    return results
+
+
+@app.post("/api/daily-summaries", response_model=DailySummaryResponse)
+def create_daily_summary(data: DailySummaryCreate, db: Session = Depends(get_db)):
+    project = get_project_by_code_or_name(db, data.project)
+    mix_query = None
+    if data.mix:
+        mix_query = data.mix
+    elif data.psi:
+        mix_query = str(data.psi)
+    elif project.default_mix:
+        mix_query = project.default_mix.code
+    mix = get_mix_by_code_or_psi(db, mix_query)
+
+    summary = db.query(DailySummary).filter(
+        DailySummary.date == data.date,
+        DailySummary.project_id == project.id,
+        DailySummary.mix_id == mix.id
+    ).first()
+
+    if summary:
+        summary.total_m3 = data.total_m3
+        summary.trips = data.trips
+        summary.driver_count = data.driver_count
+        summary.driver_daily_pay = data.driver_daily_pay
+        summary.psi = mix.psi
+        summary.mix_id = mix.id
+    else:
+        summary = DailySummary(
+            date=data.date,
+            project_id=project.id,
+            mix_id=mix.id,
+            psi=mix.psi,
+            total_m3=data.total_m3,
+            trips=data.trips,
+            driver_count=data.driver_count,
+            driver_daily_pay=data.driver_daily_pay
+        )
+        db.add(summary)
+
+    db.commit()
+    db.refresh(summary)
+
+    return {
+        "id": summary.id,
+        "date": summary.date,
+        "project_code": project.code,
+        "project_name": project.name,
+        "psi": summary.psi,
+        "total_m3": summary.total_m3,
+        "trips": summary.trips
+    }
+
+
 # ============================================================
 # 報表 API
 # ============================================================
 
+@app.get("/api/reports/summary")
+def report_summary(
+    start_date: date,
+    end_date: Optional[date] = None,
+    driver_count: Optional[int] = None,
+    driver_daily_pay: Optional[float] = None,
+    db: Session = Depends(get_db),
+):
+    """自選日期區間的彙總報表。"""
+    end_date = end_date or start_date
+    return build_range_summary(db, start_date, end_date, driver_count, driver_daily_pay)
+
+
 @app.get("/api/reports/daily")
 def report_daily(
     date_str: str,
+    driver_count: Optional[int] = None,
+    driver_daily_pay: Optional[float] = None,
     db: Session = Depends(get_db)
 ):
     """日報表"""
-    dispatches = db.query(Dispatch).filter(
-        Dispatch.date == date_str,
-        Dispatch.status != "cancelled"
-    ).all()
-    
-    summary = {
-        "date": date_str,
-        "total_trips": len(dispatches),
-        "total_m3": sum(d.load_m3 for d in dispatches),
-        "total_revenue": sum(d.total_revenue for d in dispatches),
-        "total_cost": sum(d.total_cost for d in dispatches),
-        "gross_profit": sum(d.gross_profit for d in dispatches),
-    }
-    
-    by_project = {}
-    for d in dispatches:
-        key = d.project.code
-        if key not in by_project:
-            by_project[key] = {
-                "project_name": d.project.name,
-                "trips": 0, "m3": 0, "revenue": 0, "cost": 0, "profit": 0
-            }
-        by_project[key]["trips"] += 1
-        by_project[key]["m3"] += d.load_m3
-        by_project[key]["revenue"] += d.total_revenue
-        by_project[key]["cost"] += d.total_cost
-        by_project[key]["profit"] += d.gross_profit
-    
-    return {
-        "summary": summary,
-        "by_project": by_project
-    }
+    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
+    return report_summary(target_date, target_date, driver_count, driver_daily_pay, db)
 
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
-    
+    summaries = db.query(DailySummary).join(Project).filter(
+        extract('year', DailySummary.date) == year,
+        extract('month', DailySummary.date) == month,
+    ).all()
+
     summary = {
         "year": year,
         "month": month,
-        "total_trips": len(dispatches),
-        "total_m3": sum(d.load_m3 for d in dispatches),
+        "total_trips": len(dispatches) + sum(s.trips for s in summaries),
+        "total_m3": sum(d.load_m3 for d in dispatches) + sum(s.total_m3 for s in summaries),
         "total_revenue": sum(d.total_revenue for d in dispatches),
         "total_cost": sum(d.total_cost for d in dispatches),
         "gross_profit": sum(d.gross_profit for d in dispatches),
     }
-    
+
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
-    
+
+    for s in summaries:
+        key = s.project.code
+        if key not in by_project:
+            by_project[key] = {
+                "project_name": s.project.name,
+                "trips": 0, "m3": 0, "revenue": 0, "cost": 0, "profit": 0
+            }
+        by_project[key]["trips"] += s.trips
+        by_project[key]["m3"] += s.total_m3
+
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
-    
+
+    for s in summaries:
+        key = s.date.day
+        if key not in by_day:
+            by_day[key] = {"trips": 0, "m3": 0, "revenue": 0, "profit": 0}
+        by_day[key]["trips"] += s.trips
+        by_day[key]["m3"] += s.total_m3
+
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
-    
+
     dispatches = query.order_by(Dispatch.date).all()
-    
+    summaries = db.query(DailySummary).filter(
+        DailySummary.project_id == project.id
+    )
+    if start_date:
+        summaries = summaries.filter(DailySummary.date >= start_date)
+    if end_date:
+        summaries = summaries.filter(DailySummary.date <= end_date)
+    summaries = summaries.order_by(DailySummary.date).all()
+
     return {
         "project": {
             "code": project.code,
             "name": project.name,
             "default_distance_km": project.default_distance_km,
         },
         "summary": {
-            "total_trips": len(dispatches),
-            "total_m3": sum(d.load_m3 for d in dispatches),
+            "total_trips": len(dispatches) + sum(s.trips for s in summaries),
+            "total_m3": sum(d.load_m3 for d in dispatches) + sum(s.total_m3 for s in summaries),
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
-        } for d in dispatches]
+        } for d in dispatches],
+        "daily_summaries": [{
+            "date": s.date.isoformat(),
+            "psi": s.psi,
+            "total_m3": s.total_m3,
+            "trips": s.trips
+        } for s in summaries]
     }
 
 
 # ============================================================
 # 設定 API
 # ============================================================
 
-@app.get("/api/settings")
+@app.get("/api/settings", response_model=List[SettingResponse])
 def list_settings(db: Session = Depends(get_db)):
     """列出所有設定"""
     settings = db.query(Setting).all()
-    return {s.key: s.value for s in settings}
+    return [SettingResponse(key=s.key, value=s.value) for s in settings]
+
 
 @app.put("/api/settings/{key}")
-def update_setting(key: str, value: str, db: Session = Depends(get_db)):
+def update_setting(key: str, data: SettingUpdate, db: Session = Depends(get_db)):
     """更新設定"""
     setting = db.query(Setting).filter(Setting.key == key).first()
     if setting:
-        setting.value = value
+        setting.value = data.value
     else:
-        setting = Setting(key=key, value=value)
+        setting = Setting(key=key, value=data.value)
         db.add(setting)
-    
+
     db.commit()
-    return {"status": "ok"}
+    return {"status": "ok", "key": key, "value": setting.value}
 
 
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
@@ -949,389 +1354,352 @@ def get_main_page_html():
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
-        <div class="grid" id="stats-grid" style="margin-bottom: 20px;">
+        <div class="card" style="margin-bottom:20px;">
+            <div class="form-row" style="margin-bottom:10px;">
+                <div class="form-group">
+                    <label>開始日期</label>
+                    <input type="date" id="stat-start">
+                </div>
+                <div class="form-group">
+                    <label>結束日期</label>
+                    <input type="date" id="stat-end">
+                </div>
+                <div class="form-group">
+                    <label>出勤司機數</label>
+                    <input type="number" id="stat-driver-count" min="0" value="0">
+                </div>
+                <div class="form-group">
+                    <label>司機日薪 (元)</label>
+                    <input type="number" id="stat-driver-pay" min="0" value="2500">
+                </div>
+                <div class="form-group" style="display:flex; align-items:flex-end;">
+                    <button class="btn btn-primary" onclick="loadStats()">重新計算</button>
+                </div>
+            </div>
+
+            <div class="grid" id="stats-grid">
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
+            </div>
         </div>
         
         <!-- 主功能區 -->
         <div class="tabs">
-            <button class="tab active" onclick="showTab('dispatch')">📥 快速出車</button>
-            <button class="tab" onclick="showTab('records')">📋 出車紀錄</button>
-            <button class="tab" onclick="showTab('master')">⚙️ 基礎資料</button>
+            <button class="tab active" onclick="showTab(event, 'dispatch')">📥 快速出車</button>
+            <button class="tab" onclick="showTab(event, 'records')">📋 出車紀錄</button>
+            <button class="tab" onclick="showTab(event, 'master')">⚙️ 基礎資料</button>
         </div>
         
         <!-- 快速出車 -->
         <div id="tab-dispatch" class="card">
             <h2>📥 快速出車登錄</h2>
-            <p style="color:#666; margin-bottom:20px;">選擇日期和工程後，只需輸入每車的「車號/司機」和「載量」</p>
-            
+            <p style="color:#666; margin-bottom:20px;">只輸入總出貨量與車次，不需逐車登錄司機資訊。</p>
+
             <div class="form-row">
                 <div class="form-group">
                     <label>📅 日期</label>
-                    <input type="date" id="dispatch-date">
+                    <input type="date" id="summary-date">
                 </div>
                 <div class="form-group wide">
                     <label>🏗️ 工程</label>
-                    <select id="dispatch-project"><option>載入中...</option></select>
+                    <select id="summary-project"><option>載入中...</option></select>
+                </div>
+                <div class="form-group">
+                    <label>配比</label>
+                    <select id="summary-mix"><option>載入中...</option></select>
                 </div>
                 <div class="form-group">
-                    <label>預設強度</label>
-                    <input type="text" id="default-psi" value="3000">
+                    <label>總出貨量 (m³)</label>
+                    <input type="number" id="summary-total-m3" step="0.5" value="0" oninput="renderTripSummary()">
                 </div>
             </div>
-            
-            <h3 style="margin: 20px 0 10px;">🚚 車次明細</h3>
-            <table id="dispatch-table">
-                <thead>
-                    <tr>
-                        <th style="width:40px">#</th>
-                        <th>車號/司機</th>
-                        <th style="width:100px">載量(m³)</th>
-                        <th style="width:100px">強度</th>
-                        <th style="width:100px">距離(km)</th>
-                        <th style="width:60px">操作</th>
-                    </tr>
-                </thead>
-                <tbody id="dispatch-body"></tbody>
-            </table>
-            
+
+            <div class="card" style="background:#f8f9ff; border:1px solid #e5e7eb;">
+                <div class="form-row" style="align-items:center;">
+                    <div class="form-group">
+                        <label>車次數量</label>
+                        <div style="display:flex; gap:8px; align-items:center;">
+                            <button class="btn btn-secondary" onclick="updateTripCount(-5)">-5</button>
+                            <button class="btn btn-secondary" onclick="updateTripCount(-1)">-1</button>
+                            <span id="trip-count" style="font-size:22px; font-weight:700; color:#4b5563; width:60px; text-align:center;">0</span>
+                            <button class="btn btn-secondary" onclick="updateTripCount(1)">+1</button>
+                            <button class="btn btn-secondary" onclick="updateTripCount(5)">+5</button>
+                        </div>
+                    </div>
+                    <div class="form-group" style="flex:1;">
+                        <label>今日概況</label>
+                        <div style="display:flex; gap:20px; flex-wrap:wrap; color:#4b5563;">
+                            <div>車次：<strong id="summary-trips">0</strong> 趟</div>
+                            <div>總量：<strong id="summary-total">0</strong> m³</div>
+                            <div>預估總距離：<strong id="summary-distance">0</strong> km</div>
+                        </div>
+                    </div>
+                </div>
+            </div>
+
             <div style="margin-top:20px; display:flex; gap:10px;">
-                <button class="btn btn-primary" onclick="addRow()">+ 新增一行</button>
-                <button class="btn btn-primary" onclick="addRows(5)">+ 新增五行</button>
-                <button class="btn btn-success" onclick="previewDispatch()">👁️ 預覽</button>
+                <button class="btn btn-success" onclick="saveDailySummary()">💾 紀錄</button>
+                <button class="btn btn-secondary" onclick="resetSummaryForm()">↺ 重填</button>
             </div>
         </div>
         
-        <!-- 預覽結果 -->
-        <div id="result-area" class="card">
-            <h2>📊 預覽結果</h2>
-            <div id="result-summary"></div>
-            <table id="result-table">
-                <thead id="result-thead"></thead>
-                <tbody id="result-tbody"></tbody>
-            </table>
-            <button class="btn btn-success" onclick="commitDispatch()" style="margin-top:20px;">✅ 確認寫入</button>
-        </div>
-        
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
-        // 初始化
         const today = new Date().toISOString().split('T')[0];
-        document.getElementById('dispatch-date').value = today;
+        document.getElementById('summary-date').value = today;
         document.getElementById('query-start').value = today;
         document.getElementById('query-end').value = today;
-        
-        let currentBatch = null;
-        let projects = [], trucks = [], mixes = [];
-        
-        // 載入資料
+        document.getElementById('stat-start').value = today;
+        document.getElementById('stat-end').value = today;
+
+        let projects = [], trucks = [], mixes = [], tripCount = 0;
+
         async function loadData() {
             projects = await fetch('/api/projects').then(r => r.json());
             trucks = await fetch('/api/trucks').then(r => r.json());
             mixes = await fetch('/api/mixes').then(r => r.json());
-            
-            // 填充下拉選單
+
             const projectOptions = projects.map(p => `<option value="${p.code}">${p.name} (${p.code})</option>`).join('');
-            document.getElementById('dispatch-project').innerHTML = '<option value="">請選擇</option>' + projectOptions;
+            document.getElementById('summary-project').innerHTML = '<option value="">請選擇</option>' + projectOptions;
             document.getElementById('query-project').innerHTML = '<option value="">全部</option>' + projectOptions;
-            
-            // 更新計數
+
+            const mixOptions = mixes.map(m => `<option value="${m.code}">${m.code} (${m.psi} PSI${m.name ? ' - '+m.name : ''})</option>`).join('');
+            document.getElementById('summary-mix').innerHTML = '<option value="">請選擇</option>' + mixOptions;
+
             document.getElementById('project-count').textContent = projects.length;
             document.getElementById('truck-count').textContent = trucks.length;
             document.getElementById('mix-count').textContent = mixes.length;
-            
-            // 列出資料
-            document.getElementById('project-list').innerHTML = projects.map(p => 
+
+            document.getElementById('project-list').innerHTML = projects.map(p =>
                 `<div style="padding:8px; border-bottom:1px solid #eee;">${p.code} - ${p.name}</div>`
             ).join('');
-            document.getElementById('truck-list').innerHTML = trucks.map(t => 
+            document.getElementById('truck-list').innerHTML = trucks.map(t =>
                 `<div style="padding:8px; border-bottom:1px solid #eee;">${t.code} - ${t.plate_no} (${t.driver_name || '-'})</div>`
             ).join('');
-            document.getElementById('mix-list').innerHTML = mixes.map(m => 
+            document.getElementById('mix-list').innerHTML = mixes.map(m =>
                 `<div style="padding:8px; border-bottom:1px solid #eee;">${m.code} - ${m.psi}psi</div>`
             ).join('');
-            
-            // 載入今日統計
-            loadTodayStats();
+
+            renderTripSummary();
+            loadStats();
         }
-        
-        async function loadTodayStats() {
+
+        async function loadStats() {
             try {
-                const data = await fetch(`/api/reports/daily?date_str=${today}`).then(r => r.json());
+                const start = document.getElementById('stat-start').value;
+                const end = document.getElementById('stat-end').value;
+                const driverCount = parseInt(document.getElementById('stat-driver-count').value || '0');
+                const driverPay = parseFloat(document.getElementById('stat-driver-pay').value || '0');
+                const params = new URLSearchParams({ start_date: start, end_date: end, driver_count: driverCount, driver_daily_pay: driverPay });
+                const data = await fetch(`/api/reports/summary?${params.toString()}`).then(r => r.json());
                 document.getElementById('stat-trips').textContent = data.summary.total_trips;
                 document.getElementById('stat-m3').textContent = data.summary.total_m3.toFixed(1) + ' m³';
-                document.getElementById('stat-revenue').textContent = '$' + data.summary.total_revenue.toLocaleString();
-                document.getElementById('stat-profit').textContent = '$' + data.summary.gross_profit.toLocaleString();
+                document.getElementById('stat-revenue').textContent = '$' + Math.round(data.summary.total_revenue).toLocaleString();
+                document.getElementById('stat-profit').textContent = '$' + Math.round(data.summary.gross_profit).toLocaleString();
             } catch(e) {
                 console.log('No data for today');
             }
         }
-        
-        loadData();
-        for(let i=0; i<3; i++) addRow();
-        
-        function showTab(name) {
+
+        function showTab(evt, name) {
             document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
-            event.target.classList.add('active');
+            evt.target.classList.add('active');
             document.querySelectorAll('[id^="tab-"]').forEach(el => el.style.display = 'none');
             document.getElementById('tab-' + name).style.display = 'block';
-            document.getElementById('result-area').style.display = 'none';
         }
-        
-        function addRow() {
-            const tbody = document.getElementById('dispatch-body');
-            const n = tbody.children.length + 1;
-            const psi = document.getElementById('default-psi').value;
-            
-            const tr = document.createElement('tr');
-            tr.innerHTML = `
-                <td>${n}</td>
-                <td><input class="dispatch-input item-truck" placeholder="車號或司機"></td>
-                <td><input class="dispatch-input item-load" type="number" step="0.5" placeholder="8"></td>
-                <td><input class="dispatch-input item-psi" value="${psi}"></td>
-                <td><input class="dispatch-input item-distance" type="number" step="0.1" placeholder="預設"></td>
-                <td><button class="btn btn-danger" onclick="this.closest('tr').remove()" style="padding:5px 10px;">✕</button></td>
-            `;
-            tbody.appendChild(tr);
+
+        function getSelectedProject() {
+            const code = document.getElementById('summary-project').value;
+            return projects.find(p => p.code === code);
         }
-        
-        function addRows(n) { for(let i=0; i<n; i++) addRow(); }
-        
-        function collectItems() {
-            const rows = document.querySelectorAll('#dispatch-body tr');
-            const items = [];
-            rows.forEach(tr => {
-                const truck = tr.querySelector('.item-truck').value.trim();
-                const load = tr.querySelector('.item-load').value;
-                if (truck && load) {
-                    items.push({
-                        truck,
-                        load: parseFloat(load),
-                        psi: tr.querySelector('.item-psi').value || null,
-                        distance: tr.querySelector('.item-distance').value ? parseFloat(tr.querySelector('.item-distance').value) : null
-                    });
-                }
-            });
-            return items;
+
+        function renderTripSummary() {
+            const totalM3 = parseFloat(document.getElementById('summary-total-m3').value || '0');
+            const project = getSelectedProject();
+            const distance = project ? project.default_distance_km || 0 : 0;
+            document.getElementById('trip-count').textContent = tripCount;
+            document.getElementById('summary-trips').textContent = tripCount;
+            document.getElementById('summary-total').textContent = totalM3.toFixed(1);
+            document.getElementById('summary-distance').textContent = (distance * tripCount).toFixed(1);
         }
-        
-        async function previewDispatch() {
-            const date = document.getElementById('dispatch-date').value;
-            const project = document.getElementById('dispatch-project').value;
-            const items = collectItems();
-            
-            if (!date || !project) { alert('請選擇日期和工程'); return; }
-            if (!items.length) { alert('請輸入車次資料'); return; }
-            
-            currentBatch = { date, project, items };
-            
-            const res = await fetch('/api/dispatch/preview', {
-                method: 'POST',
-                headers: {'Content-Type': 'application/json'},
-                body: JSON.stringify(currentBatch)
-            });
-            const data = await res.json();
-            showResults(data);
+
+        function updateTripCount(delta) {
+            tripCount = Math.max(0, tripCount + delta);
+            renderTripSummary();
         }
-        
-        function showResults(data) {
-            const okCount = data.filter(d => d.status === 'OK').length;
-            const errCount = data.length - okCount;
-            const totalRevenue = data.filter(d => d.status === 'OK').reduce((s, d) => s + d.total_revenue, 0);
-            const totalProfit = data.filter(d => d.status === 'OK').reduce((s, d) => s + d.gross_profit, 0);
-            
-            document.getElementById('result-summary').innerHTML = `
-                <p>共 ${data.length} 筆：<span class="status-ok">✓ ${okCount} 成功</span>
-                ${errCount ? `<span class="status-error"> ✕ ${errCount} 錯誤</span>` : ''}
-                | 預估收入: $${totalRevenue.toLocaleString()} | 預估毛利: <span class="${totalProfit >= 0 ? 'profit-positive' : 'profit-negative'}">$${totalProfit.toLocaleString()}</span></p>
-            `;
-            
-            document.getElementById('result-thead').innerHTML = `
-                <tr><th>#</th><th>狀態</th><th>工程</th><th>車號</th><th>司機</th><th>載量</th><th>收入</th><th>成本</th><th>毛利</th><th>錯誤</th></tr>
-            `;
-            
-            document.getElementById('result-tbody').innerHTML = data.map((d, i) => `
-                <tr class="${d.status === 'ERROR' ? 'status-error' : ''}">
-                    <td>${i+1}</td>
-                    <td class="${d.status === 'OK' ? 'status-ok' : ''}">${d.status}</td>
-                    <td>${d.project_name || '-'}</td>
-                    <td>${d.truck_plate || '-'}</td>
-                    <td>${d.driver_name || '-'}</td>
-                    <td>${d.load_m3} m³</td>
-                    <td>$${(d.total_revenue || 0).toLocaleString()}</td>
-                    <td>$${(d.total_cost || 0).toLocaleString()}</td>
-                    <td class="${(d.gross_profit || 0) >= 0 ? 'profit-positive' : 'profit-negative'}">$${(d.gross_profit || 0).toLocaleString()}</td>
-                    <td>${d.error || ''}</td>
-                </tr>
-            `).join('');
-            
-            document.getElementById('result-area').style.display = 'block';
+
+        function resetSummaryForm() {
+            document.getElementById('summary-total-m3').value = 0;
+            tripCount = 0;
+            renderTripSummary();
         }
-        
-        async function commitDispatch() {
-            if (!currentBatch) return;
-            if (!confirm(`確定寫入 ${currentBatch.items.length} 筆資料？`)) return;
-            
-            const res = await fetch('/api/dispatch/commit', {
+
+        async function saveDailySummary() {
+            const date = document.getElementById('summary-date').value;
+            const project = document.getElementById('summary-project').value;
+            const mix = document.getElementById('summary-mix').value;
+            const total_m3 = parseFloat(document.getElementById('summary-total-m3').value || '0');
+            const driverCount = parseInt(document.getElementById('stat-driver-count').value || '0');
+            const driverPay = parseFloat(document.getElementById('stat-driver-pay').value || '0');
+
+            if (!date || !project || !mix) { alert('請選擇日期、工程與配比'); return; }
+            if (total_m3 <= 0) { alert('請輸入總出貨量'); return; }
+
+            const res = await fetch('/api/daily-summaries', {
                 method: 'POST',
                 headers: {'Content-Type': 'application/json'},
-                body: JSON.stringify(currentBatch)
+                body: JSON.stringify({ date, project, mix, total_m3, trips: tripCount, driver_count: driverCount || null, driver_daily_pay: driverPay || null })
             });
-            const data = await res.json();
-            
-            if (data.success) {
-                alert(`✅ 成功寫入 ${data.inserted} 筆！\\n編號：${data.dispatch_nos.join(', ')}`);
-                document.getElementById('dispatch-body').innerHTML = '';
-                for(let i=0; i<3; i++) addRow();
-                document.getElementById('result-area').style.display = 'none';
-                loadTodayStats();
+
+            if (res.ok) {
+                alert('✅ 已儲存');
+                resetSummaryForm();
+                loadStats();
+                queryRecords();
             } else {
-                alert(`⚠️ 部分失敗：${data.inserted} 筆成功\\n\\n${data.errors.join('\\n')}`);
+                const err = await res.json();
+                alert(`❌ 儲存失敗：${err.detail || res.statusText}`);
             }
         }
-        
+
         async function queryRecords() {
             const start = document.getElementById('query-start').value;
             const end = document.getElementById('query-end').value;
             const project = document.getElementById('query-project').value;
-            
-            let url = `/api/dispatches?start_date=${start}&end_date=${end}`;
+
+            let url = `/api/daily-summaries?start_date=${start}&end_date=${end}`;
             if (project) url += `&project_code=${project}`;
-            
+
             const data = await fetch(url).then(r => r.json());
-            
-            const total = {revenue: 0, cost: 0, profit: 0, m3: 0};
+
+            const totals = { trips: 0, m3: 0 };
             data.forEach(d => {
-                total.revenue += d.total_revenue;
-                total.cost += d.total_cost;
-                total.profit += d.gross_profit;
-                total.m3 += d.load_m3;
+                totals.trips += d.trips;
+                totals.m3 += d.total_m3;
             });
-            
+
             document.getElementById('records-result').innerHTML = `
-                <p style="margin:15px 0;">共 ${data.length} 筆 | ${total.m3.toFixed(1)} m³ | 收入 $${total.revenue.toLocaleString()} | 毛利 <span class="${total.profit >= 0 ? 'profit-positive' : 'profit-negative'}">$${total.profit.toLocaleString()}</span></p>
+                <p style="margin:15px 0;">共 ${data.length} 筆 | 車次 ${totals.trips} 趟 | ${totals.m3.toFixed(1)} m³</p>
                 <table>
-                    <thead><tr><th>日期</th><th>編號</th><th>工程</th><th>車號</th><th>司機</th><th>載量</th><th>收入</th><th>成本</th><th>毛利</th></tr></thead>
+                    <thead><tr><th>日期</th><th>工程</th><th>配比</th><th>強度</th><th>總出貨量(m³)</th><th>車次</th></tr></thead>
                     <tbody>
                         ${data.map(d => `
                             <tr>
                                 <td>${d.date}</td>
-                                <td>${d.dispatch_no}</td>
                                 <td>${d.project_name}</td>
-                                <td>${d.truck_plate}</td>
-                                <td>${d.driver_name || '-'}</td>
-                                <td>${d.load_m3} m³</td>
-                                <td>$${d.total_revenue.toLocaleString()}</td>
-                                <td>$${d.total_cost.toLocaleString()}</td>
-                                <td class="${d.gross_profit >= 0 ? 'profit-positive' : 'profit-negative'}">$${d.gross_profit.toLocaleString()}</td>
+                                <td>${d.mix_code || '-'}</td>
+                                <td>${d.psi || '-'}</td>
+                                <td>${d.total_m3.toFixed(1)}</td>
+                                <td>${d.trips}</td>
                             </tr>
                         `).join('')}
                     </tbody>
                 </table>
             `;
         }
+
+        loadData();
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
