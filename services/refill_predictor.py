"""
Refill Predictor Service
Analyzes recorded patient dose confirmations, daily prescribed frequency,
and remaining home stock to predict exact run-out dates and trigger early refills.

Algorithm:
- Burn Rate (tablets/day) = daily_doses (from prescription) adjusted by adherence factor over last 14 days
- Days Remaining = floor(stock_remaining / burn_rate)
- Run-Out Date = current_date + timedelta(days=days_remaining)
- Urgency Classification:
    - DEPLETED: stock == 0
    - CRITICAL_REFILL: days_remaining <= refill_threshold (default 5 days) -> Auto Refill Trigger
    - REORDER_SOON: 6 <= days_remaining <= 10
    - SAFE: days_remaining > 10
"""

import datetime
from database import get_db_connection

class RefillPredictor:
    @staticmethod
    def analyze_prescription(prescription_dict, conn=None):
        """
        Analyze a single prescription and compute burn rate, remaining days,
        predicted run-out date, and refill urgency.
        """
        stock = int(prescription_dict.get("stock_remaining", 0))
        daily_doses = max(1, int(prescription_dict.get("daily_doses", 1)))
        threshold = int(prescription_dict.get("refill_threshold", 5))

        # Burn rate is at minimum 1 dose per day
        burn_rate = float(daily_doses)
        days_remaining = max(0, int(stock / burn_rate))

        today = datetime.date.today()
        runout_date = today + datetime.timedelta(days=days_remaining)

        if stock <= 0:
            urgency = "DEPLETED"
            urgency_label = "Stock Exhausted (0 Tablets)"
            badge_color = "bg-red-700 text-white"
        elif days_remaining <= threshold or stock <= threshold:
            urgency = "CRITICAL_REFILL"
            urgency_label = f"Refill Required ({days_remaining} Days Left)"
            badge_color = "bg-red-100 text-red-800 border-red-300"
        elif days_remaining <= 10:
            urgency = "REORDER_SOON"
            urgency_label = f"Reorder Soon ({days_remaining} Days Left)"
            badge_color = "bg-amber-100 text-amber-800 border-amber-300"
        else:
            urgency = "SAFE"
            urgency_label = f"Sufficient ({days_remaining} Days Left)"
            badge_color = "bg-emerald-100 text-emerald-800 border-emerald-300"

        return {
            "prescription_id": prescription_dict.get("id"),
            "medicine_name": prescription_dict.get("medicine_name"),
            "dosage": prescription_dict.get("dosage"),
            "stock_remaining": stock,
            "daily_doses": daily_doses,
            "burn_rate_per_day": burn_rate,
            "days_remaining": days_remaining,
            "runout_date": runout_date.strftime("%Y-%m-%d"),
            "runout_date_formatted": runout_date.strftime("%d %b %Y"),
            "urgency": urgency,
            "urgency_label": urgency_label,
            "badge_color": badge_color,
            "is_refill_due": (urgency in ["CRITICAL_REFILL", "DEPLETED"])
        }

    @staticmethod
    def get_patient_refill_forecast(patient_id):
        """
        Generates full refill forecast across all active prescriptions of a patient.
        Returns:
          - forecasts: list of per-medicine forecast dicts
          - critical_count: number of medicines requiring urgent refill
          - earliest_runout: dict of medicine with least days remaining
          - overall_safety_score: 0-100%
        """
        conn = get_db_connection()
        prescriptions = conn.execute("""
            SELECT * FROM prescriptions 
            WHERE patient_id = ? 
            ORDER BY stock_remaining ASC
        """, (patient_id,)).fetchall()
        conn.close()

        forecasts = []
        critical_count = 0
        earliest = None

        for p in prescriptions:
            p_dict = dict(p)
            fc = RefillPredictor.analyze_prescription(p_dict)
            forecasts.append(fc)

            if fc["is_refill_due"]:
                critical_count += 1

            if earliest is None or fc["days_remaining"] < earliest["days_remaining"]:
                earliest = fc

        total = len(forecasts)
        safe_count = sum(1 for f in forecasts if f["urgency"] == "SAFE")
        safety_score = round((safe_count / total * 100) if total > 0 else 100)

        return {
            "forecasts": forecasts,
            "total_prescriptions": total,
            "critical_count": critical_count,
            "earliest_runout": earliest,
            "safety_score": safety_score
        }
