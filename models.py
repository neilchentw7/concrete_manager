"""
預拌混凝土出車管理系統 v2 - 資料庫模型

設計原則：
1. 簡化價格表：工程 × 配比 = 一個單價（不再按載量分）
2. 出車紀錄預存所有計算結果（不用每次查詢都重算）
3. 清晰的外鍵關係
4. 支援軟刪除（is_active）
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from enum import Enum

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, 
    Date, DateTime, ForeignKey, Text, Index, Numeric,
    event, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session
from sqlalchemy.sql import func

# ============================================================
# Database Setup
# ============================================================

DB_PATH = "concrete_v2.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ============================================================
# 1. 工程/案場 (Project)
# ============================================================

class Project(Base):
    """
    工程/案場資料
    
    簡化設計：
    - 預設距離存在這裡（不用每次出車都填）
    - 短少補貼門檻和金額也在這裡
    """
    __tablename__ = "projects"
    
    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False, index=True, comment="工程代碼 (如 BIG01)")
    name = Column(String(100), nullable=False, comment="工程名稱")
    
    # 案場資訊
    address = Column(String(200), comment="地址")
    contact_name = Column(String(50), comment="聯絡人")
    contact_phone = Column(String(20), comment="聯絡電話")
    
    # 預設值（出車時自動帶入）
    default_distance_km = Column(Float, default=10.0, comment="預設距離(單程公里)")
    default_mix_id = Column(Integer, ForeignKey("mixes.id"), nullable=True, comment="預設配比")
    
    # 短少補貼設定
    subsidy_threshold_m3 = Column(Float, default=6.0, comment="補貼門檻(低於此載量給補貼)")
    subsidy_amount = Column(Float, default=500.0, comment="補貼金額")
    
    # 狀態
    is_active = Column(Boolean, default=True, comment="是否啟用")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    note = Column(Text, comment="備註")
    
    # 關聯
    prices = relationship("ProjectPrice", back_populates="project", cascade="all, delete-orphan")
    dispatches = relationship("Dispatch", back_populates="project")
    default_mix = relationship("Mix", foreign_keys=[default_mix_id])
    
    def __repr__(self):
        return f"<Project {self.code}: {self.name}>"


# ============================================================
# 2. 材料單價 (MaterialPrice)
# ============================================================

class MaterialPrice(Base):
    """
    材料單價表
    
    管理各種原料的單價，用於計算配比成本
    """
    __tablename__ = "material_prices"
    
    id = Column(Integer, primary_key=True)
    price_id = Column(String(20), unique=True, nullable=False, index=True, comment="價格代碼 (如 2025Y)")
    name = Column(String(50), comment="名稱 (如 2025年度)")
    
    # 各材料單價 (元/kg)
    sand_price = Column(Float, default=0.0, comment="砂 $/kg")
    stone_price = Column(Float, default=0.0, comment="石 $/kg")
    cement_price = Column(Float, default=0.0, comment="水泥 $/kg")
    slag_price = Column(Float, default=0.0, comment="爐石 $/kg")
    flyash_price = Column(Float, default=0.0, comment="飛灰 $/kg")
    admixture_price = Column(Float, default=0.0, comment="藥劑 $/kg")
    
    # 生效期間
    effective_from = Column(Date, comment="生效日期")
    effective_to = Column(Date, comment="結束日期")
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    note = Column(Text)
    
    def __repr__(self):
        return f"<MaterialPrice {self.price_id}: {self.name}>"
    
    def to_dict(self):
        return {
            "sand": self.sand_price,
            "stone": self.stone_price,
            "cement": self.cement_price,
            "slag": self.slag_price,
            "flyash": self.flyash_price,
            "admixture": self.admixture_price
        }


# ============================================================
# 3. 配比 (Mix)
# ============================================================

class Mix(Base):
    """
    混凝土配比
    
    包含完整的材料用量（每立方公尺）
    材料成本 = Σ(用量 × 單價)
    """
    __tablename__ = "mixes"
    
    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False, index=True, comment="配比代碼 (如 3002)")
    psi = Column(Integer, nullable=False, index=True, comment="強度 (如 3000, 4000)")
    name = Column(String(50), comment="配比名稱")
    
    # 關聯的材料單價
    material_price_id = Column(Integer, ForeignKey("material_prices.id"), nullable=True, comment="材料單價")
    
    # 材料用量 (kg/m³)
    sand1_kg = Column(Float, default=0.0, comment="砂1 kg/m³")
    sand2_kg = Column(Float, default=0.0, comment="砂2 kg/m³")
    stone1_kg = Column(Float, default=0.0, comment="石1 kg/m³")
    stone2_kg = Column(Float, default=0.0, comment="石2 kg/m³")
    cement_kg = Column(Float, default=0.0, comment="水泥 kg/m³")
    slag_kg = Column(Float, default=0.0, comment="爐石 kg/m³")
    flyash_kg = Column(Float, default=0.0, comment="飛灰 kg/m³")
    admixture_kg = Column(Float, default=0.0, comment="藥劑 kg/m³")
    
    # 計算後的成本（每次材料單價更新時重算）
    material_cost_per_m3 = Column(Float, default=0.0, comment="材料成本 $/m³")
    
    # 舊欄位（相容性）
    material_detail = Column(Text, comment="材料明細 JSON (舊)")
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    note = Column(Text)
    
    # 關聯
    material_price = relationship("MaterialPrice")
    prices = relationship("ProjectPrice", back_populates="mix")
    dispatches = relationship("Dispatch", back_populates="mix")
    
    def __repr__(self):
        return f"<Mix {self.code}: {self.psi}psi>"
    
    def calc_material_cost(self, mp: "MaterialPrice" = None) -> float:
        """
        計算材料成本
        
        Args:
            mp: 材料單價物件，若未提供則使用關聯的 material_price
        """
        if mp is None:
            mp = self.material_price
        if mp is None:
            return self.material_cost_per_m3 or 0.0
        
        # 砂 = sand1 + sand2
        sand_cost = (self.sand1_kg + self.sand2_kg) * mp.sand_price
        # 石 = stone1 + stone2
        stone_cost = (self.stone1_kg + self.stone2_kg) * mp.stone_price
        # 其他材料
        cement_cost = self.cement_kg * mp.cement_price
        slag_cost = self.slag_kg * mp.slag_price
        flyash_cost = self.flyash_kg * mp.flyash_price
        admixture_cost = self.admixture_kg * mp.admixture_price
        
        return sand_cost + stone_cost + cement_cost + slag_cost + flyash_cost + admixture_cost
    
    def get_material_breakdown(self, mp: "MaterialPrice" = None) -> dict:
        """取得材料成本明細"""
        if mp is None:
            mp = self.material_price
        if mp is None:
            return {}
        
        return {
            "砂": {"用量": self.sand1_kg + self.sand2_kg, "單價": mp.sand_price, "小計": (self.sand1_kg + self.sand2_kg) * mp.sand_price},
            "石": {"用量": self.stone1_kg + self.stone2_kg, "單價": mp.stone_price, "小計": (self.stone1_kg + self.stone2_kg) * mp.stone_price},
            "水泥": {"用量": self.cement_kg, "單價": mp.cement_price, "小計": self.cement_kg * mp.cement_price},
            "爐石": {"用量": self.slag_kg, "單價": mp.slag_price, "小計": self.slag_kg * mp.slag_price},
            "飛灰": {"用量": self.flyash_kg, "單價": mp.flyash_price, "小計": self.flyash_kg * mp.flyash_price},
            "藥劑": {"用量": self.admixture_kg, "單價": mp.admixture_price, "小計": self.admixture_kg * mp.admixture_price},
        }


# ============================================================
# 3. 工程單價表 (ProjectPrice)
# ============================================================

class ProjectPrice(Base):
    """
    工程單價表
    
    簡化設計：
    - 一個工程 + 一個配比 = 一個單價
    - 不再按載量分（載量不同用同一單價，短少有補貼）
    """
    __tablename__ = "project_prices"
    
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    mix_id = Column(Integer, ForeignKey("mixes.id"), nullable=False)
    
    # 單價
    price_per_m3 = Column(Float, nullable=False, comment="單價 $/m³")
    
    # 生效期間（可選，用於價格調整）
    effective_from = Column(Date, comment="生效日期")
    effective_to = Column(Date, comment="結束日期")
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    note = Column(Text)
    
    # 關聯
    project = relationship("Project", back_populates="prices")
    mix = relationship("Mix", back_populates="prices")
    
    # 唯一約束：同一工程+配比+生效期間只能有一筆
    __table_args__ = (
        UniqueConstraint('project_id', 'mix_id', 'effective_from', name='uq_project_mix_date'),
        Index('ix_project_price_lookup', 'project_id', 'mix_id', 'is_active'),
    )
    
    def __repr__(self):
        return f"<ProjectPrice project={self.project_id} mix={self.mix_id} ${self.price_per_m3}/m³>"


# ============================================================
# 4. 車輛 (Truck)
# ============================================================

class Truck(Base):
    """
    車輛資料
    """
    __tablename__ = "trucks"
    
    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False, index=True, comment="車輛代碼 (如 D01)")
    plate_no = Column(String(20), nullable=False, comment="車牌號碼")
    
    # 司機資訊
    driver_name = Column(String(50), comment="司機姓名")
    driver_phone = Column(String(20), comment="司機電話")
    
    # 成本參數
    default_load_m3 = Column(Float, default=8.0, comment="預設載量 m³")
    fuel_l_per_km = Column(Float, default=0.5, comment="油耗 L/km")
    driver_pay_per_trip = Column(Float, default=800.0, comment="司機每趟工資")
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    note = Column(Text)
    
    # 關聯
    dispatches = relationship("Dispatch", back_populates="truck")
    
    def __repr__(self):
        return f"<Truck {self.code}: {self.plate_no} ({self.driver_name})>"


# ============================================================
# 5. 系統設定 (Setting)
# ============================================================

class Setting(Base):
    """
    系統設定（油價等可變參數）
    """
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True)
    key = Column(String(50), unique=True, nullable=False)
    value = Column(String(200), nullable=False)
    description = Column(String(200))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<Setting {self.key}={self.value}>"


# ============================================================
# 6. 出車紀錄 (Dispatch)
# ============================================================

class Dispatch(Base):
    """
    出車紀錄
    
    重點設計：
    - 預存所有計算結果（收入、成本、毛利）
    - 查詢報表時不需要重算
    - 保留原始輸入值，方便追蹤
    """
    __tablename__ = "dispatches"
    
    id = Column(Integer, primary_key=True)
    
    # 出車編號（自動產生：MMDD + 工程代碼 + 序號）
    dispatch_no = Column(String(20), unique=True, nullable=False, index=True)
    
    # 基本資料
    date = Column(Date, nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    mix_id = Column(Integer, ForeignKey("mixes.id"), nullable=False)
    truck_id = Column(Integer, ForeignKey("trucks.id"), nullable=False)
    
    # 出車數據
    load_m3 = Column(Float, nullable=False, comment="載量 m³")
    distance_km = Column(Float, nullable=False, comment="距離(單程) km")
    
    # ===== 以下為計算欄位（寫入時自動計算）=====
    
    # 單價（寫入時從 ProjectPrice 查詢）
    price_per_m3 = Column(Float, comment="當時單價 $/m³")
    
    # 收入
    revenue = Column(Float, default=0.0, comment="收入 = 載量 × 單價")
    subsidy = Column(Float, default=0.0, comment="短少補貼")
    total_revenue = Column(Float, default=0.0, comment="總收入 = 收入 + 補貼")
    
    # 成本
    material_cost = Column(Float, default=0.0, comment="材料成本")
    fuel_cost = Column(Float, default=0.0, comment="油料成本")
    driver_cost = Column(Float, default=0.0, comment="司機成本")
    other_cost = Column(Float, default=0.0, comment="其他成本")
    total_cost = Column(Float, default=0.0, comment="總成本")
    
    # 毛利
    gross_profit = Column(Float, default=0.0, comment="毛利 = 總收入 - 總成本")
    profit_margin = Column(Float, default=0.0, comment="毛利率 %")
    
    # 當時的油價（記錄用）
    fuel_price = Column(Float, comment="當時油價 $/L")
    
    # 狀態與備註
    status = Column(String(20), default="completed", comment="狀態: completed/cancelled")
    note = Column(Text)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # 關聯
    project = relationship("Project", back_populates="dispatches")
    mix = relationship("Mix", back_populates="dispatches")
    truck = relationship("Truck", back_populates="dispatches")
    
    # 索引
    __table_args__ = (
        Index('ix_dispatch_date_project', 'date', 'project_id'),
        Index('ix_dispatch_date_truck', 'date', 'truck_id'),
    )
    
    def __repr__(self):
        return f"<Dispatch {self.dispatch_no}: {self.date} {self.load_m3}m³>"


# ============================================================
# 6. 日出貨彙總 (DailySummary)
# ============================================================

class DailySummary(Base):
    """按日期與工程儲存彙總出貨資料（不記錄個別車次）。"""

    __tablename__ = "daily_summaries"

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    psi = Column(Integer, nullable=True, comment="預拌強度 (PSI)")
    total_m3 = Column(Float, nullable=False, comment="當日總出貨 m³")
    trips = Column(Integer, nullable=False, default=0, comment="車次數")
    note = Column(Text)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    project = relationship("Project")

    __table_args__ = (
        Index('ix_summary_date_project', 'date', 'project_id'),
    )


# ============================================================
# Database Initialization
# ============================================================

def init_db():
    """建立所有資料表"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """取得資料庫 Session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def reset_db():
    """重置資料庫（刪除所有資料表後重建）"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


# ============================================================
# 初始化設定值
# ============================================================

def init_default_settings(db: Session):
    """初始化預設設定"""
    defaults = [
        ("fuel_price", "32.5", "當前油價 $/L"),
        ("default_psi", "3000", "預設強度"),
        ("default_load_m3", "8", "預設載量 m³"),
        ("driver_daily_salary", "0", "司機每日薪資"),
        ("driver_count", "0", "司機人數"),
    ]
    
    for key, value, desc in defaults:
        existing = db.query(Setting).filter(Setting.key == key).first()
        if not existing:
            db.add(Setting(key=key, value=value, description=desc))
    
    db.commit()


if __name__ == "__main__":
    print("初始化資料庫...")
    init_db()
    
    db = SessionLocal()
    init_default_settings(db)
    db.close()
    
    print(f"✅ 資料庫已建立: {DB_PATH}")
