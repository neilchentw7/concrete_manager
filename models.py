diff --git a/models.py b/models.py
index cecea4c3a7456a63fdd23108ca92b30e801de270..97c23295c4ee910fcbe011cc8c19a24cedf27c16 100644
--- a/models.py
+++ b/models.py
@@ -367,50 +367,80 @@ class Dispatch(Base):
     
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
 
 
+# ============================================================
+# 6. 日出貨彙總 (DailySummary)
+# ============================================================
+
+class DailySummary(Base):
+    """按日期與工程儲存彙總出貨資料（不記錄個別車次）。"""
+
+    __tablename__ = "daily_summaries"
+
+    id = Column(Integer, primary_key=True)
+    date = Column(Date, nullable=False, index=True)
+    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
+    mix_id = Column(Integer, ForeignKey("mixes.id"), nullable=True)
+    psi = Column(Integer, nullable=True, comment="預拌強度 (PSI)")
+    total_m3 = Column(Float, nullable=False, comment="當日總出貨 m³")
+    trips = Column(Integer, nullable=False, default=0, comment="車次數")
+    driver_count = Column(Integer, nullable=True, default=None, comment="當日出勤司機數")
+    driver_daily_pay = Column(Float, nullable=True, default=None, comment="單位司機日薪")
+    note = Column(Text)
+    created_at = Column(DateTime, default=func.now())
+    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
+
+    project = relationship("Project")
+    mix = relationship("Mix")
+
+    __table_args__ = (
+        Index('ix_summary_date_project', 'date', 'project_id'),
+    )
+
+
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
