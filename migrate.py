"""
資料遷移工具

從舊資料庫 (concrete_profit.db) 遷移到新架構 (concrete_v2.db)
"""

import sqlite3
from datetime import datetime

from models import (
    init_db, SessionLocal, reset_db,
    Project, Mix, Truck, ProjectPrice, Dispatch, Setting
)


def migrate_from_old_db(old_db_path: str = "../concrete_system/concrete_profit.db"):
    """
    從舊資料庫遷移資料
    """
    print("=" * 60)
    print("開始資料遷移")
    print("=" * 60)
    
    # 連接舊資料庫
    old_conn = sqlite3.connect(old_db_path)
    old_conn.row_factory = sqlite3.Row
    old_cur = old_conn.cursor()
    
    # 初始化新資料庫
    reset_db()
    db = SessionLocal()
    
    # 初始化設定
    settings = [
        ("fuel_price", "32.5", "當前油價 $/L"),
        ("default_psi", "3000", "預設強度"),
        ("default_load_m3", "8", "預設載量 m³"),
        ("driver_daily_salary", "0", "司機每日薪資"),
        ("driver_count", "0", "司機人數"),
    ]
    for key, value, desc in settings:
        db.add(Setting(key=key, value=value, description=desc))
    db.commit()
    print("✓ 設定值已初始化")
    
    # --------------------------------------------------------
    # 1. 遷移配比 (mix_designs → mixes)
    # --------------------------------------------------------
    print("\n遷移配比...")
    old_cur.execute("""
        SELECT md.mix_id, md.psi,
               md.sand1_kg_m3, md.sand2_kg_m3, md.stone1_kg_m3, md.stone2_kg_m3,
               md.cement_kg_m3, md.slag_kg_m3, md.flyash_kg_m3, md.admixture_kg_m3,
               mp.sand1_price, mp.sand2_price, mp.stone1_price, mp.stone2_price,
               mp.cement_price, mp.slag_price, mp.flyash_price, mp.admixture_price
        FROM mix_designs md
        LEFT JOIN material_prices mp ON md.material_price_id = mp.id
    """)
    
    mix_id_map = {}  # old mix_id → new mix.id
    
    for row in old_cur.fetchall():
        # 計算材料成本
        material_cost = (
            (row['sand1_kg_m3'] or 0) * (row['sand1_price'] or 0) +
            (row['sand2_kg_m3'] or 0) * (row['sand2_price'] or 0) +
            (row['stone1_kg_m3'] or 0) * (row['stone1_price'] or 0) +
            (row['stone2_kg_m3'] or 0) * (row['stone2_price'] or 0) +
            (row['cement_kg_m3'] or 0) * (row['cement_price'] or 0) +
            (row['slag_kg_m3'] or 0) * (row['slag_price'] or 0) +
            (row['flyash_kg_m3'] or 0) * (row['flyash_price'] or 0) +
            (row['admixture_kg_m3'] or 0) * (row['admixture_price'] or 0)
        )
        
        mix = Mix(
            code=row['mix_id'],
            psi=row['psi'] or 3000,
            name=f"{row['psi']}psi" if row['psi'] else row['mix_id'],
            material_cost_per_m3=round(material_cost, 2)
        )
        db.add(mix)
        db.flush()
        mix_id_map[row['mix_id']] = mix.id
    
    db.commit()
    print(f"  ✓ 遷移 {len(mix_id_map)} 個配比")
    
    # --------------------------------------------------------
    # 2. 遷移車輛 (trucks → trucks)
    # --------------------------------------------------------
    print("\n遷移車輛...")
    old_cur.execute("SELECT * FROM trucks")
    
    truck_id_map = {}  # old truck_id → new truck.id
    
    for row in old_cur.fetchall():
        truck = Truck(
            code=row['truck_id'],
            plate_no=row['truck_no'],
            driver_name=row['driver_name'],
            default_load_m3=8.0,
            fuel_l_per_km=row['fuel_l_per_km'] or 0.5,
            driver_pay_per_trip=row['driver_daily_pay'] or 800.0
        )
        db.add(truck)
        db.flush()
        truck_id_map[row['truck_id']] = truck.id
    
    db.commit()
    print(f"  ✓ 遷移 {len(truck_id_map)} 輛車")
    
    # --------------------------------------------------------
    # 3. 遷移工程 (projects → projects)
    # --------------------------------------------------------
    print("\n遷移工程...")
    old_cur.execute("SELECT * FROM projects")
    
    project_id_map = {}  # old project_id → new project.id
    project_old_id_map = {}  # old projects.id → new project.id
    
    for row in old_cur.fetchall():
        # 從出車紀錄找預設距離
        old_cur.execute("""
            SELECT AVG(distance_km_oneway) as avg_dist
            FROM dispatch_logs
            WHERE project_id_fk = ?
        """, (row['id'],))
        dist_row = old_cur.fetchone()
        default_distance = dist_row['avg_dist'] if dist_row and dist_row['avg_dist'] else 10.0
        
        project = Project(
            code=row['project_id'],
            name=row['name'],
            default_distance_km=round(default_distance, 1),
            subsidy_threshold_m3=6.0,
            subsidy_amount=500.0
        )
        db.add(project)
        db.flush()
        project_id_map[row['project_id']] = project.id
        project_old_id_map[row['id']] = project.id
    
    db.commit()
    print(f"  ✓ 遷移 {len(project_id_map)} 個工程")
    
    # --------------------------------------------------------
    # 4. 遷移單價表 (price_tables → project_prices)
    # --------------------------------------------------------
    print("\n遷移單價...")
    
    # 先取得舊的 mix_designs id 對照
    old_cur.execute("SELECT id, mix_id FROM mix_designs")
    old_mix_id_to_code = {row['id']: row['mix_id'] for row in old_cur.fetchall()}
    
    old_cur.execute("""
        SELECT DISTINCT project_id_fk, mix_design_id_fk, price_per_truck, load_m3
        FROM price_tables
        WHERE is_subsidy = 0
        ORDER BY project_id_fk, mix_design_id_fk, load_m3 DESC
    """)
    
    # 每個工程+配比只取一個單價（取最大載量的）
    price_cache = {}
    for row in old_cur.fetchall():
        key = (row['project_id_fk'], row['mix_design_id_fk'])
        if key not in price_cache:
            price_cache[key] = row
    
    price_count = 0
    for (old_proj_id, old_mix_id), row in price_cache.items():
        if old_proj_id not in project_old_id_map:
            continue
        
        new_project_id = project_old_id_map[old_proj_id]
        
        # 找到對應的新 mix_id
        old_mix_code = old_mix_id_to_code.get(old_mix_id)
        if not old_mix_code or old_mix_code not in mix_id_map:
            continue
        
        new_mix_id = mix_id_map[old_mix_code]
        
        # 計算每 m³ 單價
        load_m3 = row['load_m3'] or 8.0
        price_per_m3 = (row['price_per_truck'] or 0) / load_m3 if load_m3 > 0 else 0
        
        # 檢查是否已存在
        existing = db.query(ProjectPrice).filter(
            ProjectPrice.project_id == new_project_id,
            ProjectPrice.mix_id == new_mix_id
        ).first()
        
        if not existing:
            price = ProjectPrice(
                project_id=new_project_id,
                mix_id=new_mix_id,
                price_per_m3=round(price_per_m3, 2)
            )
            db.add(price)
            price_count += 1
    
    db.commit()
    print(f"  ✓ 遷移 {price_count} 筆單價")
    
    # --------------------------------------------------------
    # 5. 遷移出車紀錄 (dispatch_logs → dispatches)
    # --------------------------------------------------------
    print("\n遷移出車紀錄...")
    
    old_cur.execute("SELECT id, truck_id FROM trucks")
    old_truck_id_to_code = {row['id']: row['truck_id'] for row in old_cur.fetchall()}
    
    from datetime import datetime as dt
    
    old_cur.execute("""
        SELECT dl.*, pt.price_per_truck, pt.subsidy_amount
        FROM dispatch_logs dl
        LEFT JOIN price_tables pt ON dl.price_table_id_fk = pt.id
    """)
    
    dispatch_count = 0
    for row in old_cur.fetchall():
        # 取得對應的新 ID
        if row['project_id_fk'] not in project_old_id_map:
            continue
        
        new_project_id = project_old_id_map[row['project_id_fk']]
        project = db.query(Project).filter(Project.id == new_project_id).first()
        
        old_mix_code = old_mix_id_to_code.get(row['mix_design_id_fk'])
        if not old_mix_code or old_mix_code not in mix_id_map:
            continue
        new_mix_id = mix_id_map[old_mix_code]
        mix = db.query(Mix).filter(Mix.id == new_mix_id).first()
        
        old_truck_code = old_truck_id_to_code.get(row['truck_id_fk'])
        if not old_truck_code or old_truck_code not in truck_id_map:
            continue
        new_truck_id = truck_id_map[old_truck_code]
        truck = db.query(Truck).filter(Truck.id == new_truck_id).first()
        
        # 解析日期
        date_str = row['date']
        if isinstance(date_str, str):
            dispatch_date = dt.strptime(date_str, "%Y-%m-%d").date()
        else:
            dispatch_date = date_str
        
        # 計算
        load_m3 = row['load_m3'] or 8.0
        distance_km = row['distance_km_oneway'] or 10.0
        fuel_price = row['fuel_price_day'] or 32.5
        price_per_truck = row['price_per_truck'] or 0
        subsidy = row['subsidy_amount'] or 0
        
        price_per_m3 = price_per_truck / load_m3 if load_m3 > 0 else 0
        revenue = load_m3 * price_per_m3
        total_revenue = revenue + subsidy
        
        material_cost = load_m3 * (mix.material_cost_per_m3 or 0)
        fuel_cost = distance_km * 2 * (truck.fuel_l_per_km or 0.5) * fuel_price
        driver_cost = truck.driver_pay_per_trip or 800.0
        total_cost = material_cost + fuel_cost + driver_cost
        
        gross_profit = total_revenue - total_cost
        profit_margin = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0
        
        dispatch = Dispatch(
            dispatch_no=row['dispatch_id'],
            date=dispatch_date,
            project_id=new_project_id,
            mix_id=new_mix_id,
            truck_id=new_truck_id,
            load_m3=load_m3,
            distance_km=distance_km,
            price_per_m3=round(price_per_m3, 2),
            revenue=round(revenue, 2),
            subsidy=round(subsidy, 2),
            total_revenue=round(total_revenue, 2),
            material_cost=round(material_cost, 2),
            fuel_cost=round(fuel_cost, 2),
            driver_cost=round(driver_cost, 2),
            total_cost=round(total_cost, 2),
            gross_profit=round(gross_profit, 2),
            profit_margin=round(profit_margin, 2),
            fuel_price=fuel_price
        )
        db.add(dispatch)
        dispatch_count += 1
    
    db.commit()
    print(f"  ✓ 遷移 {dispatch_count} 筆出車紀錄")
    
    # 關閉連接
    old_conn.close()
    db.close()
    
    print("\n" + "=" * 60)
    print("✅ 資料遷移完成！")
    print("=" * 60)
    
    return {
        "mixes": len(mix_id_map),
        "trucks": len(truck_id_map),
        "projects": len(project_id_map),
        "prices": price_count,
        "dispatches": dispatch_count
    }


if __name__ == "__main__":
    import sys
    
    old_db = sys.argv[1] if len(sys.argv) > 1 else "../concrete_system/concrete_profit.db"
    
    try:
        result = migrate_from_old_db(old_db)
        print(f"\n遷移統計：")
        print(f"  配比：{result['mixes']}")
        print(f"  車輛：{result['trucks']}")
        print(f"  工程：{result['projects']}")
        print(f"  單價：{result['prices']}")
        print(f"  出車：{result['dispatches']}")
    except FileNotFoundError:
        print(f"❌ 找不到舊資料庫：{old_db}")
    except Exception as e:
        print(f"❌ 遷移失敗：{e}")
        raise
