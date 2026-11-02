"""
Inventory Manager Service
Handles pharmacy warehouse stock reservation, overselling prevention,
cancellation stock restoration, and patient home inventory restock.

Scientific note:
- 'pharmacy_stock' in medicines table represents stock reported by the configured dispensing pharmacy.
- 'stock_remaining' in prescriptions table represents estimated tablets at the senior's home,
  updated strictly upon reported dose confirmation or delivery receipt.
"""

import sqlite3
from database import get_db_connection

class InventoryManager:
    @staticmethod
    def check_availability(medicine_id, requested_qty):
        """
        Check if requested quantity is available in pharmacy inventory.
        Returns: (is_available, current_stock, unit_price, requires_rx, med_name)
        """
        conn = get_db_connection()
        med = conn.execute("SELECT id, name, price, pharmacy_stock, requires_prescription FROM medicines WHERE id = ?", (medicine_id,)).fetchone()
        conn.close()

        if not med:
            return False, 0, 0.0, False, "Unknown Medicine"

        stock = med["pharmacy_stock"] or 0
        is_avail = stock >= requested_qty
        return is_avail, stock, med["price"], bool(med["requires_prescription"]), med["name"]

    @staticmethod
    def reserve_stock_for_order(order_id, items, conn=None):
        """
        Atomically reserve pharmacy inventory for an order and create normalized order_items.
        Prevents overselling using SQL condition 'WHERE pharmacy_stock >= requested_qty'.
        
        items: list of dicts with keys:
          - medicine_id (int)
          - medicine_name (str)
          - dosage (str)
          - quantity (int)
          - unit_price (float)
          - requires_prescription (bool)
        
        Returns: (success: bool, error_msg: str)
        """
        local_conn = False
        if conn is None:
            conn = get_db_connection()
            local_conn = True

        cursor = conn.cursor()
        try:
            if not items:
                return False, "An order must contain at least one item."
            # Validate the full request before any stock mutation to prevent
            # negative quantities, non-finite prices, and partial reservations.
            import math
            validated_items = []
            for item in items:
                qty = int(item.get("quantity", 30))
                if item.get("unit_price") is None:
                    return False, "A verified unit price is required."
                unit_price = float(item["unit_price"])
                if qty <= 0 or qty > 100000 or not math.isfinite(unit_price) or unit_price < 0:
                    return False, "Invalid order quantity or price."
                validated_items.append((item, qty, unit_price))

            # Guard against duplicate reservation for the same order
            existing_items = cursor.execute("SELECT COUNT(*) FROM order_items WHERE order_id = ?", (order_id,)).fetchone()[0]
            if existing_items > 0:
                if local_conn:
                    conn.close()
                return False, f"Duplicate reservation rejected: stock already reserved for order #{order_id}."

            for item, qty, unit_price in validated_items:
                med_id = item.get("medicine_id")
                med_name = item.get("medicine_name", "Medicine")
                dosage = item.get("dosage", "Standard")
                requires_rx = 1 if item.get("requires_prescription") else 0
                subtotal = qty * unit_price

                # Check and atomically decrement pharmacy stock if available
                if med_id:
                    cursor.execute("""
                        UPDATE medicines 
                        SET pharmacy_stock = pharmacy_stock - ? 
                        WHERE id = ? AND pharmacy_stock >= ?
                    """, (qty, med_id, qty))

                    if cursor.rowcount == 0:
                        # Insufficient stock or concurrency conflict
                        if local_conn:
                            conn.rollback()
                            conn.close()
                        return False, f"Insufficient pharmacy stock for '{med_name}'. Cannot fulfill requested {qty} tablets."
                
                # Insert normalized order item
                cursor.execute("""
                    INSERT INTO order_items (
                        order_id, medicine_id, medicine_name, dosage,
                        quantity, unit_price, subtotal, requires_prescription
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (order_id, med_id, med_name, dosage, qty, unit_price, subtotal, requires_rx))

            if local_conn:
                conn.commit()
                conn.close()
            return True, "Stock reserved successfully."

        except Exception as e:
            if local_conn:
                conn.rollback()
                conn.close()
            return False, f"Inventory reservation failed ({type(e).__name__})."

    @staticmethod
    def restore_stock_on_cancellation(order_id, cancellation_reason="Cancelled by User or Pharmacist"):
        """
        Restores reserved pharmacy inventory when an order is cancelled prior to delivery.
        Ensures inventory integrity and records audit trail.
        """
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            order = conn.execute("SELECT * FROM refill_orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                conn.close()
                return False, "Order not found."

            if order["status"] == "Delivered":
                conn.close()
                return False, "Delivered orders cannot be cancelled."

            if order["is_cancelled"] == 1:
                conn.close()
                return False, "Order is already cancelled."

            transitioned = cursor.execute("""
                UPDATE refill_orders
                SET status = 'Cancelled', is_cancelled = 1, cancellation_reason = ?
                WHERE id = ? AND is_cancelled = 0 AND status NOT IN ('Delivered', 'Cancelled')
            """, (cancellation_reason, order_id))
            if transitioned.rowcount != 1:
                conn.rollback()
                conn.close()
                return False, "Order state changed; cancellation was not applied."

            # Fetch order items to restore
            items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
            restored_count = 0

            for item in items:
                if item["medicine_id"]:
                    cursor.execute("""
                        UPDATE medicines 
                        SET pharmacy_stock = pharmacy_stock + ? 
                        WHERE id = ?
                    """, (item["quantity"], item["medicine_id"]))
                    restored_count += item["quantity"]

            # Record in order status history with timestamp and actor
            cursor.execute("""
                INSERT INTO order_status_history (order_id, status, notes, updated_by)
                VALUES (?, 'Cancelled', ?, ?)
            """, (order_id, f"Order cancelled: {cancellation_reason}. {restored_count} tablets restored to warehouse stock.", "User / Staff Request"))

            # Record alert in caregiver audit
            cursor.execute("""
                INSERT INTO caregiver_alerts (patient_id, alert_type, message)
                VALUES (?, 'ORDER_CANCELLED', ?)
            """, (
                order["patient_id"],
                f"Order #{order['order_number']} has been cancelled. Reason: {cancellation_reason}. {restored_count} reserved tablets restored to pharmacy stock."
            ))

            conn.commit()
            conn.close()
            return True, f"Order #{order['order_number']} cancelled. {restored_count} tablets restored to pharmacy stock."

        except Exception as e:
            conn.rollback()
            conn.close()
            return False, f"Cancellation failed ({type(e).__name__})."

    @staticmethod
    def restore_stock_on_failed_delivery(order_id, reason="Delivery attempt failed", updated_by="Delivery staff"):
        """Record a failed final-mile attempt and restore reserved pharmacy stock once."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            order = conn.execute("SELECT * FROM refill_orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                conn.close()
                return False, "Order not found."
            if order["status"] not in {"Shipped", "Out for Delivery"} or order["is_cancelled"]:
                conn.close()
                return False, "Only an active dispatched order can be marked as failed delivery."
            claimed = cursor.execute("""
                UPDATE refill_orders SET status = 'Failed Delivery', is_cancelled = 1,
                  cancellation_reason = ?
                WHERE id = ? AND status IN ('Shipped', 'Out for Delivery') AND is_cancelled = 0
            """, (reason[:500], order_id))
            if claimed.rowcount != 1:
                conn.rollback()
                conn.close()
                return False, "Order state changed; failed delivery was not applied."
            items = conn.execute("SELECT medicine_id, quantity FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
            restored = 0
            for item in items:
                if item["medicine_id"]:
                    cursor.execute("UPDATE medicines SET pharmacy_stock = pharmacy_stock + ? WHERE id = ?", (item["quantity"], item["medicine_id"]))
                    restored += item["quantity"]
            cursor.execute("""
                INSERT INTO order_status_history (order_id, status, notes, updated_by)
                VALUES (?, 'Failed Delivery', ?, ?)
            """, (order_id, f"Delivery attempt failed: {reason[:500]}. {restored} reserved units restored to pharmacy stock.", updated_by[:120]))
            cursor.execute("""
                INSERT INTO caregiver_alerts (patient_id, alert_type, message)
                VALUES (?, 'FAILED_DELIVERY', ?)
            """, (order["patient_id"], f"Order {order['order_number']} was marked Failed Delivery. Contact the dispensing pharmacy to arrange next steps."))
            conn.commit()
            conn.close()
            return True, f"Order {order['order_number']} marked Failed Delivery; reserved stock restored."
        except Exception as exc:
            conn.rollback()
            conn.close()
            return False, f"Failed-delivery update error: {type(exc).__name__}."

    @staticmethod
    def restock_home_inventory_on_delivery(order_id):
        """
        When delivery partner marks order as 'Delivered', automatically increments
        the patient's home inventory (prescriptions table) by the delivered quantities.
        """
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            order = conn.execute("SELECT * FROM refill_orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                conn.close()
                return False, "Order not found"

            if order["status"] != "Delivered":
                conn.close()
                return False, "Only delivered orders can restock home inventory."

            claimed = cursor.execute("""
                UPDATE refill_orders SET delivery_restocked = 1
                WHERE id = ? AND status = 'Delivered' AND delivery_restocked = 0
            """, (order_id,))
            if claimed.rowcount != 1:
                conn.rollback()
                conn.close()
                return False, "Home inventory was already restocked for this delivery."

            patient_id = order["patient_id"]
            items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()

            for item in items:
                qty = item["quantity"]
                med_name = item["medicine_name"]

                # Match by medicine_id or medicine_name for the patient
                if item["medicine_id"]:
                    cursor.execute("""
                        UPDATE prescriptions 
                        SET stock_remaining = stock_remaining + ? 
                        WHERE patient_id = ? AND (medicine_id = ? OR medicine_name LIKE ?)
                    """, (qty, patient_id, item["medicine_id"], f"%{med_name.split('(')[0].strip()}%"))
                else:
                    cursor.execute("""
                        UPDATE prescriptions 
                        SET stock_remaining = stock_remaining + ? 
                        WHERE patient_id = ? AND medicine_name LIKE ?
                    """, (qty, patient_id, f"%{med_name.split('(')[0].strip()}%"))

            conn.commit()
            conn.close()
            return True, "Home inventory successfully restocked."
        except Exception as e:
            conn.rollback()
            conn.close()
            return False, f"Home-stock update failed ({type(e).__name__})."
