"""
出車計算引擎

負責：
1. 模糊比對（工程、車輛、配比）
2. 自動計算收入、成本、毛利
3. 產生出車編號
4. 資料驗證
"""

from datetime import date, datetime
from typing import Optional, Dict, Any, List, Tuple
import difflib
import re

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func

from models import Project, Mix, Truck, ProjectPrice, Dispatch, Setting, DailySummary, DriverAttendance


class DispatchCalculator:
    """出車計算引擎"""
    
    def __init__(self, db: Session):
        self.db = db
        self._dispatch_no_cache: Dict[Tuple[int, date], set] = {}
    
    # ========================================
    # 設定值取得
    # ========================================
    
    def get_setting(self, key: str, default: str = "") -> str:
        """取得系統設定值"""
        setting = self.db.query(Setting).filter(Setting.key == key).first()
        return setting.value if setting else default
    
    def get_fuel_price(self) -> float:
        """取得當前油價"""
        return float(self.get_setting("fuel_price", "32.5"))
    
    # ========================================
    # 模糊比對
    # ========================================
    
    @staticmethod
    def normalize(s: str) -> str:
        """標準化字串"""
        if not s:
            return ""
        return str(s).strip().upper()
    
    def fuzzy_match(self, query: str, candidates: List[str], cutoff: float = 0.6) -> Optional[str]:
        """模糊比對"""
        if not query or not candidates:
            return None
        
        query = self.normalize(query)
        
        # 完全比對
        for c in candidates:
            if self.normalize(c) == query:
                return c
        
        # 模糊比對
        matches = difflib.get_close_matches(
            query, 
            [self.normalize(c) for c in candidates], 
            n=1, 
            cutoff=cutoff
        )
        
        if matches:
            # 找回原始字串
            for c in candidates:
                if self.normalize(c) == matches[0]:
                    return c
        
        return None
    
    def find_project(self, query: str) -> Project:
        """查找工程（支援代碼或名稱模糊比對）"""
        projects = self.db.query(Project).filter(Project.is_active == True).all()
        
        if not projects:
            raise ValueError("資料庫中沒有任何工程")
        
        # 建立候選清單
        candidates = {}
        for p in projects:
            candidates[p.code] = p
            candidates[p.name] = p
        
        matched = self.fuzzy_match(query, list(candidates.keys()))
        
        if not matched:
            raise ValueError(f"找不到工程：{query}")
        
        return candidates[matched]
    
    def find_truck(self, query: str) -> Truck:
        """查找車輛（支援代碼、車牌、司機名模糊比對）"""
        trucks = self.db.query(Truck).filter(Truck.is_active == True).all()
        
        if not trucks:
            raise ValueError("資料庫中沒有任何車輛")
        
        candidates = {}
        for t in trucks:
            candidates[t.code] = t
            candidates[t.plate_no] = t
            if t.driver_name:
                candidates[t.driver_name] = t
        
        matched = self.fuzzy_match(query, list(candidates.keys()), cutoff=0.5)
        
        if not matched:
            raise ValueError(f"找不到車輛：{query}")
        
        return candidates[matched]
    
    def find_mix(self, query: str) -> Mix:
        """查找配比（支援代碼或 PSI）"""
        mixes = self.db.query(Mix).filter(Mix.is_active == True).all()
        
        if not mixes:
            raise ValueError("資料庫中沒有任何配比")
        
        # 嘗試解析 PSI
        psi = self.parse_psi(query)
        if psi:
            for m in mixes:
                if m.psi == psi:
                    return m
        
        # 用代碼比對
        candidates = {m.code: m for m in mixes}
        matched = self.fuzzy_match(query, list(candidates.keys()))
        
        if matched:
            return candidates[matched]
        
        raise ValueError(f"找不到配比：{query}")
    
    # ========================================
    # 解析工具
    # ========================================
    
    def parse_date(self, raw: str) -> date:
        """解析日期"""
        if isinstance(raw, date):
            return raw
        
        if not raw:
            raise ValueError("日期不可為空")
        
        s = str(raw).strip()
        
        formats = [
            "%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M", 
            "%Y-%m-%d %H:%M", "%m/%d", "%Y%m%d"
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(s, fmt)
                if fmt == "%m/%d":
                    dt = dt.replace(year=datetime.now().year)
                return dt.date()
            except:
                continue
        
        raise ValueError(f"無法解析日期：{raw}")
    
    def parse_psi(self, raw: str) -> Optional[int]:
        """解析強度"""
        if not raw:
            return None
        
        s = str(raw).lower().replace("psi", "").strip()
        digits = re.sub(r"\D", "", s)
        
        if not digits:
            return None
        
        # 30 -> 3000, 40 -> 4000
        if len(digits) <= 2:
            digits += "00"
        
        return int(digits)
    
    # ========================================
    # 出車編號產生
    # ========================================
    
    def generate_dispatch_no(self, project: Project, dispatch_date: date) -> str:
        """
        產生出車編號
        格式：MMDD + 工程代碼 + 序號(2位)
        例：0115BIG0101
        """
        cache_key = (project.id, dispatch_date)
        
        if cache_key not in self._dispatch_no_cache:
            # 查詢當日該工程已有的編號
            prefix = f"{dispatch_date.month:02d}{dispatch_date.day:02d}{project.code}"
            
            existing = self.db.query(Dispatch.dispatch_no).filter(
                Dispatch.dispatch_no.like(f"{prefix}%")
            ).all()
            
            self._dispatch_no_cache[cache_key] = {r[0] for r in existing}
        
        used = self._dispatch_no_cache[cache_key]
        prefix = f"{dispatch_date.month:02d}{dispatch_date.day:02d}{project.code}"
        
        seq = 1
        while True:
            candidate = f"{prefix}{seq:02d}"
            if candidate not in used:
                used.add(candidate)
                return candidate
            seq += 1
    
    # ========================================
    # 單價查詢
    # ========================================
    
    def get_price(self, project: Project, mix: Mix, dispatch_date: date, load_m3: float) -> float:
        """取得單價，若有載運區間則依載量匹配。"""
        price = (
            self.db.query(ProjectPrice)
            .filter(
                ProjectPrice.project_id == project.id,
                ProjectPrice.mix_id == mix.id,
                ProjectPrice.is_active == True,
                or_(
                    ProjectPrice.effective_from == None,
                    ProjectPrice.effective_from <= dispatch_date
                ),
                or_(
                    ProjectPrice.effective_to == None,
                    ProjectPrice.effective_to >= dispatch_date
                ),
                or_(ProjectPrice.load_min_m3 == None, ProjectPrice.load_min_m3 <= load_m3),
                or_(ProjectPrice.load_max_m3 == None, ProjectPrice.load_max_m3 >= load_m3)
            )
            .order_by(
                ProjectPrice.load_min_m3.desc().nulls_last(),
                ProjectPrice.effective_from.desc().nulls_last()
            )
            .first()
        )

        if not price:
            raise ValueError(
                f"找不到單價：工程={project.code}, 配比={mix.code}, 載量={load_m3}m³"
            )

        return price.price_per_m3
    
    # ========================================
    # 成本計算
    # ========================================
    
    def calculate_costs(
        self,
        project: Project,
        mix: Mix,
        truck: Truck,
        load_m3: float,
        distance_km: float,
        fuel_price: Optional[float] = None,
        dispatch_date: Optional[date] = None,
        include_current_trip: bool = False,
    ) -> Dict[str, Any]:
        """
        計算所有成本
        
        Returns:
            {
                "material_cost": 材料成本,
                "fuel_cost": 油料成本,
                "driver_cost": 司機成本,
                "total_cost": 總成本,
                "details": {"material": {...}, "fuel": {...}, "driver": {...}, "total_formula": str}
            }
        """
        if fuel_price is None:
            fuel_price = self.get_fuel_price()
        
        # 材料成本 = 載量 × 每 m³ 材料成本
        material_cost = load_m3 * (mix.material_cost_per_m3 or 0)
        material_detail = {
            "load_m3": load_m3,
            "cost_per_m3": round(mix.material_cost_per_m3 or 0, 2),
            "formula": f"{load_m3} m³ × {round(mix.material_cost_per_m3 or 0, 2)} = {round(material_cost, 2)}",
            "amount": round(material_cost, 2)
        }

        # 油料成本 = 距離(來回) × 油耗 × 油價
        fuel_cost = (distance_km * 2) * (truck.fuel_l_per_km or 0.5) * fuel_price
        fuel_detail = {
            "distance_round_trip_km": round(distance_km * 2, 2),
            "fuel_l_per_km": round(truck.fuel_l_per_km or 0.5, 2),
            "fuel_price": round(fuel_price, 2),
            "formula": f"{round(distance_km * 2, 2)} km × {round(truck.fuel_l_per_km or 0.5, 2)} L/km × {round(fuel_price, 2)} = {round(fuel_cost, 2)}",
            "amount": round(fuel_cost, 2)
        }

        # 司機成本
        driver_cost = truck.driver_pay_per_trip or 800.0
        if dispatch_date:
            driver_cost, driver_detail = self.calculate_driver_cost(
                dispatch_date,
                include_current_trip,
                default_per_trip=driver_cost
            )
        else:
            driver_detail = {
                "method": "per_trip",
                "per_trip_rate": round(driver_cost, 2),
                "formula": f"固定每趟 {round(driver_cost, 2)} 元",
                "amount": round(driver_cost, 2)
            }
        
        total_cost = material_cost + fuel_cost + driver_cost
        
        return {
            "material_cost": round(material_cost, 2),
            "fuel_cost": round(fuel_cost, 2),
            "driver_cost": round(driver_cost, 2),
            "total_cost": round(total_cost, 2),
            "details": {
                "material": material_detail,
                "fuel": fuel_detail,
                "driver": driver_detail,
                "total_formula": f"{round(material_cost, 2)} + {round(fuel_cost, 2)} + {round(driver_cost, 2)} = {round(total_cost, 2)}"
            }
        }

    def calculate_driver_cost(self, dispatch_date: date, include_current_trip: bool, default_per_trip: float) -> tuple[float, Dict[str, Any]]:
        """根據當日總車次平均分攤司機成本並回傳詳細公式。"""

        driver_daily_salary = float(self.get_setting("driver_daily_salary", "0") or 0)
        default_driver_count = int(float(self.get_setting("driver_count", "0") or 0))
        attendance_count = (
            self.db.query(DriverAttendance.driver_count)
            .filter(DriverAttendance.date == dispatch_date)
            .scalar()
        )
        driver_count = int(attendance_count) if attendance_count is not None else default_driver_count

        total_salary = driver_daily_salary * driver_count
        if total_salary <= 0:
            return round(default_per_trip, 2), {
                "method": "per_trip",
                "per_trip_rate": round(default_per_trip, 2),
                "formula": f"固定每趟 {round(default_per_trip, 2)} 元",
                "amount": round(default_per_trip, 2)
            }

        trip_query = self.db.query(Dispatch).filter(
            Dispatch.date == dispatch_date,
            Dispatch.status != "cancelled"
        )
        existing_trips = trip_query.count()

        summary_trips = (
            self.db.query(func.coalesce(func.sum(DailySummary.trips), 0))
            .filter(DailySummary.date == dispatch_date)
            .scalar()
        ) or 0

        total_trips = existing_trips + summary_trips
        if include_current_trip:
            total_trips += 1

        if total_trips <= 0:
            return round(default_per_trip, 2), {
                "method": "per_trip",
                "per_trip_rate": round(default_per_trip, 2),
                "formula": f"固定每趟 {round(default_per_trip, 2)} 元",
                "amount": round(default_per_trip, 2)
            }

        per_trip_cost = round(total_salary / total_trips, 2)
        return per_trip_cost, {
            "method": "shared_payroll",
            "driver_daily_salary": round(driver_daily_salary, 2),
            "driver_count": driver_count,
            "total_salary": round(total_salary, 2),
            "total_trips": total_trips,
            "include_current_trip": include_current_trip,
            "formula": f"({round(driver_daily_salary, 2)} × {driver_count} 人) ÷ {total_trips} 趟 = {per_trip_cost}",
            "amount": per_trip_cost
        }
    
    # ========================================
    # 收入計算
    # ========================================
    
    def calculate_revenue(
        self,
        project: Project,
        load_m3: float,
        price_per_m3: float
    ) -> Dict[str, Any]:
        """
        計算收入（含短少補貼）
        
        Returns:
            {
                "revenue": 基本收入,
                "subsidy": 短少補貼,
                "total_revenue": 總收入,
                "details": {"base": {...}, "subsidy": {...}, "total_formula": str}
            }
        """
        # 基本收入
        revenue = load_m3 * price_per_m3
        base_detail = {
            "load_m3": load_m3,
            "price_per_m3": round(price_per_m3, 2),
            "formula": f"{load_m3} m³ × {round(price_per_m3, 2)} = {round(revenue, 2)}",
            "amount": round(revenue, 2)
        }

        # 短少補貼
        subsidy = 0.0
        if load_m3 < (project.subsidy_threshold_m3 or 6.0):
            subsidy = project.subsidy_amount or 500.0
        subsidy_detail = {
            "threshold_m3": project.subsidy_threshold_m3 or 6.0,
            "subsidy_amount": round(project.subsidy_amount or 500.0, 2),
            "applied": load_m3 < (project.subsidy_threshold_m3 or 6.0),
            "formula": f"載量 {load_m3} m³ < 門檻 {project.subsidy_threshold_m3 or 6.0}，補貼 {round(subsidy, 2)}",
            "amount": round(subsidy, 2)
        }

        total_revenue = revenue + subsidy

        return {
            "revenue": round(revenue, 2),
            "subsidy": round(subsidy, 2),
            "total_revenue": round(total_revenue, 2),
            "details": {
                "base": base_detail,
                "subsidy": subsidy_detail,
                "total_formula": f"{round(revenue, 2)} + {round(subsidy, 2)} = {round(total_revenue, 2)}"
            }
        }
    
    # ========================================
    # 主要功能：建立出車紀錄
    # ========================================
    
    def create_dispatch(
        self,
        date_str: str,
        project_str: str,
        truck_str: str,
        load_m3: float,
        mix_str: Optional[str] = None,
        distance_km: Optional[float] = None,
        fuel_price: Optional[float] = None,
        note: Optional[str] = None,
        auto_commit: bool = False
    ) -> Dispatch:
        """
        建立出車紀錄
        
        Args:
            date_str: 日期
            project_str: 工程（代碼或名稱）
            truck_str: 車輛（代碼、車牌或司機名）
            load_m3: 載量
            mix_str: 配比（代碼或 PSI），預設用工程的預設配比
            distance_km: 距離，預設用工程的預設距離
            fuel_price: 油價，預設用系統設定
            note: 備註
            auto_commit: 是否自動 commit
        
        Returns:
            Dispatch 物件
        """
        # 1. 解析日期
        dispatch_date = self.parse_date(date_str)
        
        # 2. 查找工程
        project = self.find_project(project_str)
        
        # 3. 查找車輛
        truck = self.find_truck(truck_str)
        
        # 4. 查找配比（使用預設或指定）
        if mix_str:
            mix = self.find_mix(mix_str)
        elif project.default_mix:
            mix = project.default_mix
        else:
            # 用預設 PSI 查找
            default_psi = self.get_setting("default_psi", "3000")
            mix = self.find_mix(default_psi)
        
        # 5. 距離（使用預設或指定）
        if distance_km is None:
            distance_km = project.default_distance_km or 10.0
        
        # 6. 油價
        if fuel_price is None:
            fuel_price = self.get_fuel_price()
        
        # 7. 查詢單價
        price_per_m3 = self.get_price(project, mix, dispatch_date, load_m3)
        
        # 8. 計算收入
        revenue_calc = self.calculate_revenue(project, load_m3, price_per_m3)
        
        # 9. 計算成本
        cost_calc = self.calculate_costs(
            project,
            mix,
            truck,
            load_m3,
            distance_km,
            fuel_price,
            dispatch_date,
            include_current_trip=True,
        )
        
        # 10. 計算毛利
        gross_profit = revenue_calc["total_revenue"] - cost_calc["total_cost"]
        profit_margin = (gross_profit / revenue_calc["total_revenue"] * 100) if revenue_calc["total_revenue"] > 0 else 0
        
        # 11. 產生編號
        dispatch_no = self.generate_dispatch_no(project, dispatch_date)
        
        # 12. 檢查重複
        existing = self.db.query(Dispatch).filter(
            Dispatch.date == dispatch_date,
            Dispatch.project_id == project.id,
            Dispatch.truck_id == truck.id,
            Dispatch.load_m3 == load_m3,
            Dispatch.status != "cancelled"
        ).first()
        
        if existing:
            raise ValueError(f"疑似重複：同日同工程同車同載量已有紀錄 ({existing.dispatch_no})")
        
        # 13. 建立紀錄
        dispatch = Dispatch(
            dispatch_no=dispatch_no,
            date=dispatch_date,
            project_id=project.id,
            mix_id=mix.id,
            truck_id=truck.id,
            load_m3=load_m3,
            distance_km=distance_km,
            price_per_m3=price_per_m3,
            revenue=revenue_calc["revenue"],
            subsidy=revenue_calc["subsidy"],
            total_revenue=revenue_calc["total_revenue"],
            material_cost=cost_calc["material_cost"],
            fuel_cost=cost_calc["fuel_cost"],
            driver_cost=cost_calc["driver_cost"],
            total_cost=cost_calc["total_cost"],
            gross_profit=round(gross_profit, 2),
            profit_margin=round(profit_margin, 2),
            fuel_price=fuel_price,
            note=note
        )
        
        self.db.add(dispatch)
        
        if auto_commit:
            self.db.commit()
            self.db.refresh(dispatch)
        
        return dispatch
    
    # ========================================
    # 預覽功能
    # ========================================
    
    def preview_dispatch(
        self,
        date_str: str,
        project_str: str,
        truck_str: str,
        load_m3: float,
        mix_str: Optional[str] = None,
        distance_km: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        預覽出車資料（不寫入資料庫）
        
        Returns:
            預覽資料字典
        """
        try:
            dispatch_date = self.parse_date(date_str)
            project = self.find_project(project_str)
            truck = self.find_truck(truck_str)
            
            if mix_str:
                mix = self.find_mix(mix_str)
            elif project.default_mix:
                mix = project.default_mix
            else:
                mix = self.find_mix(self.get_setting("default_psi", "3000"))
            
            if distance_km is None:
                distance_km = project.default_distance_km or 10.0
            
            fuel_price = self.get_fuel_price()
            price_per_m3 = self.get_price(project, mix, dispatch_date, load_m3)
            
            revenue_calc = self.calculate_revenue(project, load_m3, price_per_m3)
            cost_calc = self.calculate_costs(
                project,
                mix,
                truck,
                load_m3,
                distance_km,
                fuel_price,
                dispatch_date,
                include_current_trip=True,
            )
            
            gross_profit = revenue_calc["total_revenue"] - cost_calc["total_cost"]

            return {
                "status": "OK",
                "date": dispatch_date.isoformat(),
                "project_code": project.code,
                "project_name": project.name,
                "truck_code": truck.code,
                "truck_plate": truck.plate_no,
                "driver_name": truck.driver_name,
                "mix_code": mix.code,
                "mix_psi": mix.psi,
                "load_m3": load_m3,
                "distance_km": distance_km,
                "price_per_m3": price_per_m3,
                "revenue": revenue_calc["revenue"],
                "subsidy": revenue_calc["subsidy"],
                "total_revenue": revenue_calc["total_revenue"],
                "revenue_details": revenue_calc.get("details", {}),
                "material_cost": cost_calc["material_cost"],
                "fuel_cost": cost_calc["fuel_cost"],
                "driver_cost": cost_calc["driver_cost"],
                "total_cost": cost_calc["total_cost"],
                "cost_details": cost_calc.get("details", {}),
                "gross_profit": round(gross_profit, 2),
                "gross_profit_formula": f"{revenue_calc['total_revenue']} - {cost_calc['total_cost']} = {round(gross_profit, 2)}",
            }
            
        except Exception as e:
            return {
                "status": "ERROR",
                "error": str(e)
            }