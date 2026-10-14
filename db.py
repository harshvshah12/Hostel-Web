import os
import json
import sqlite3
from urllib.parse import urlparse
from werkzeug.security import generate_password_hash, check_password_hash

# Check for PostgreSQL database URL from environment (Supabase, Neon, or Vercel Postgres)
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("SUPABASE_DB_URL")

# Resolve project path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# --- Password Hashing Helpers ---
def hash_pin(pin):
    """Hash raw PIN/password using Werkzeug if not already hashed."""
    if not pin:
        return ""
    pin_str = str(pin).strip()
    if pin_str.startswith(("pbkdf2:", "scrypt:", "argon2:")):
        return pin_str
    return generate_password_hash(pin_str)


def verify_pin(stored_pin, input_pin):
    """Verify input PIN against stored PIN (supports Werkzeug hash and legacy plain text)."""
    if not stored_pin or input_pin is None:
        return False
    stored_str = str(stored_pin).strip()
    input_str = str(input_pin).strip()
    if stored_str.startswith(("pbkdf2:", "scrypt:", "argon2:")):
        return check_password_hash(stored_str, input_str)
    # Legacy plain-text fallback
    return stored_str == input_str


# --- Data Classes Matching Application Structure ---
class Id:
    def __init__(self, nameR, emailR, pinR, no=0, typeRoom="None", expen=0, id=None):
        self.id = id
        self.name = nameR
        self.email = emailR
        self.pin = str(pinR)
        self.roomNo = int(no) if no is not None else 0
        self.roomType = typeRoom or "None"
        self.expenditures = float(expen) if expen is not None else 0.0
        self.myOrderedFoods = []

    def addOrderedFood(self, name, quantity, price):
        orderfood = OrderedFoods(name, int(quantity), float(price))
        self.myOrderedFoods.append(orderfood)


class OrderedFoods:
    def __init__(self, name, quantity, price, id=None):
        self.id = id
        self.namefood = name
        self.quantity = int(quantity)
        self.price = float(price)


class workers:
    def __init__(self, name, job, salary, id=None):
        self.id = id
        self.name = name
        self.job = job
        self.salary = float(salary)


class Foods:
    def __init__(self, name, price, quantity, id=None):
        self.id = id
        self.name = name
        self.price = float(price)
        self.quantity = int(quantity)


class Rooms:
    def __init__(self, typeR, priceR, totalR, book, sRoom, id=None):
        self.id = id
        self.typeRoom = typeR
        self.priceRoom = float(priceR)
        self.totalRooms = int(totalR)
        self.bookRooms = int(book)
        self.startingRoom = int(sRoom)


class RequestResidence:
    def __init__(self, name, email, pin, status="pending", created_at=None, id=None):
        self.id = id
        self.name = name
        self.email = email
        self.pin = str(pin)
        self.status = status or "pending"
        self.created_at = str(created_at) if created_at else ""


class RequestJobs:
    def __init__(self, name, email, job, id=None):
        self.id = id
        self.name = name
        self.email = email
        self.job = job


class ManagerUser:
    def __init__(self, name, email, pin, id=None):
        self.id = id
        self.name = name
        self.email = email
        self.pin = str(pin)


class OwnerUser:
    def __init__(self, name, email, pin, id=None):
        self.id = id
        self.name = name
        self.email = email
        self.pin = str(pin)


# --- Authoritative Database Layer ---
class Database:
    def __init__(self, db_path=None):
        self.is_postgres = False
        self.conn_str = DATABASE_URL

        if self.conn_str and ("postgres" in self.conn_str or "postgresql" in self.conn_str):
            # Ensure protocol is postgresql:// for psycopg2/pg8000
            if self.conn_str.startswith("postgres://"):
                self.conn_str = self.conn_str.replace("postgres://", "postgresql://", 1)
            self.is_postgres = True
            print("[DB] Using Cloud PostgreSQL / Supabase Database")
        else:
            # Enforce persistent database requirement on Vercel
            if os.environ.get("VERCEL"):
                raise RuntimeError(
                    "CRITICAL CONFIGURATION ERROR: Production deployment on Vercel requires "
                    "DATABASE_URL (or POSTGRES_URL / SUPABASE_DB_URL) to be set in Vercel Environment Variables. "
                    "Ephemeral SQLite in /tmp/hostel.db has been permanently disabled to prevent silent data loss. "
                    "Please configure your Supabase PostgreSQL connection string in the Vercel Project Settings."
                )

            if db_path:
                self.sqlite_path = db_path
            elif os.environ.get("HOSTEL_DB_PATH"):
                self.sqlite_path = os.environ.get("HOSTEL_DB_PATH")
            else:
                data_dir = os.path.join(BASE_DIR, "data")
                os.makedirs(data_dir, exist_ok=True)
                self.sqlite_path = os.path.join(data_dir, "hostel.db")
            print(f"[DB] Using Local SQLite Database at {self.sqlite_path}")

        # Vercel serverless functions may cold-start frequently. The production
        # Supabase schema is managed by migrations, so avoid running DDL and a
        # full seed scan during every module import / login request.
        if os.environ.get("VERCEL") and self.is_postgres:
            print("[DB] Skipping runtime schema bootstrap on Vercel; using migration-managed schema")
        else:
            self.init_tables()
            self.seed_if_empty()

    def get_connection(self):
        """Get an active database connection with row access."""
        if self.is_postgres:
            try:
                import psycopg2
            except ImportError:
                # Fallback only when psycopg2 is not installed. Do not retry a
                # failed network connection with a second driver and double the
                # wait time on every authentication request.
                import pg8000
                p = urlparse(self.conn_str)
                return pg8000.connect(
                    user=p.username,
                    password=p.password,
                    host=p.hostname,
                    port=p.port or 5432,
                    database=p.path.lstrip("/"),
                    timeout=5,
                    ssl_context=True
                )

            # Bound connection establishment and query execution so a database
            # outage cannot leave the browser waiting indefinitely.
            return psycopg2.connect(
                self.conn_str,
                connect_timeout=5,
                options="-c statement_timeout=10000 -c lock_timeout=5000"
            )
        else:
            conn = sqlite3.connect(self.sqlite_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            return conn

    def _format_query(self, query):
        """Translate SQL parameter syntax between Postgres (%s) and SQLite (?)."""
        if self.is_postgres:
            return query
        return query.replace("%s", "?")

    def init_tables(self):
        """Create authoritative tables and perform migrations if needed."""
        auto_inc = "SERIAL" if self.is_postgres else "INTEGER"
        pk = f"id {auto_inc} PRIMARY KEY" if self.is_postgres else "id INTEGER PRIMARY KEY AUTOINCREMENT"

        schema = f"""
        CREATE TABLE IF NOT EXISTS owners (
            {pk},
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            pin TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS managers (
            {pk},
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            pin TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS residents (
            {pk},
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            pin TEXT NOT NULL,
            room_no INTEGER DEFAULT 0,
            room_type TEXT DEFAULT 'None',
            expenditures REAL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS rooms (
            {pk},
            type_room TEXT UNIQUE NOT NULL,
            price_room REAL NOT NULL,
            total_rooms INTEGER NOT NULL,
            book_rooms INTEGER DEFAULT 0,
            starting_room INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS foods (
            {pk},
            name TEXT UNIQUE NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workers (
            {pk},
            name TEXT NOT NULL,
            job TEXT NOT NULL,
            salary REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            {pk},
            message TEXT NOT NULL,
            sender TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS complaints (
            {pk},
            complain TEXT NOT NULL,
            resident_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS residence_requests (
            {pk},
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            pin TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS job_requests (
            {pk},
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            job TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ordered_foods (
            {pk},
            food_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            resident_name TEXT NOT NULL,
            pin TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        conn = self.get_connection()
        try:
            with conn:
                cur = conn.cursor()
                for stmt in schema.strip().split(";"):
                    stmt_clean = stmt.strip()
                    if stmt_clean:
                        cur.execute(stmt_clean)

                # Migrations for existing SQLite databases (ensure status column exists)
                if not self.is_postgres:
                    try:
                        cur.execute("PRAGMA table_info(residence_requests)")
                        cols = [r[1] for r in cur.fetchall()]
                        if cols and "status" not in cols:
                            cur.execute("ALTER TABLE residence_requests ADD COLUMN status TEXT DEFAULT 'pending'")
                        if cols and "created_at" not in cols:
                            cur.execute("ALTER TABLE residence_requests ADD COLUMN created_at TEXT DEFAULT ''")
                    except Exception:
                        pass
                cur.close()
        finally:
            conn.close()

    def seed_if_empty(self):
        """Idempotently seed the Indian dataset if tables are empty. Never wipes existing data."""
        conn = self.get_connection()
        try:
            with conn:
                cur = conn.cursor()

                # 1. Seed Owner
                cur.execute(self._format_query("SELECT COUNT(*) FROM owners"))
                if cur.fetchone()[0] == 0:
                    creds_path = os.path.join(BASE_DIR, "users_credentials.json")
                    if os.path.exists(creds_path):
                        with open(creds_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        for u in data.get("users", {}).get("owner", []):
                            cur.execute(self._format_query("INSERT INTO owners (name, email, pin) VALUES (%s, %s, %s)"),
                                        (u["name"], u["email"], hash_pin(u["password"])))
                    else:
                        cur.execute(self._format_query("INSERT INTO owners (name, email, pin) VALUES (%s, %s, %s)"),
                                    ("Rajesh Sharma", "rajesh.sharma@hostel.in", hash_pin("12345")))

                # 2. Seed Managers
                cur.execute(self._format_query("SELECT COUNT(*) FROM managers"))
                if cur.fetchone()[0] == 0:
                    creds_path = os.path.join(BASE_DIR, "users_credentials.json")
                    if os.path.exists(creds_path):
                        with open(creds_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        for u in data.get("users", {}).get("managers", []):
                            cur.execute(self._format_query("INSERT INTO managers (name, email, pin) VALUES (%s, %s, %s)"),
                                        (u["name"], u["email"], hash_pin(u["password"])))

                # 3. Seed Residents
                cur.execute(self._format_query("SELECT COUNT(*) FROM residents"))
                if cur.fetchone()[0] == 0:
                    creds_path = os.path.join(BASE_DIR, "users_credentials.json")
                    if os.path.exists(creds_path):
                        with open(creds_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        for u in data.get("users", {}).get("residents", []):
                            cur.execute(self._format_query(
                                "INSERT INTO residents (name, email, pin, room_no, room_type, expenditures) VALUES (%s, %s, %s, %s, %s, %s)"),
                                (u["name"], u["email"], hash_pin(u["password"]), u.get("room_no", 0), u.get("room_type", "None"), float(u.get("expenditures_inr", 0))))

                # 4. Seed Workers
                cur.execute(self._format_query("SELECT COUNT(*) FROM workers"))
                if cur.fetchone()[0] == 0:
                    creds_path = os.path.join(BASE_DIR, "users_credentials.json")
                    if os.path.exists(creds_path):
                        with open(creds_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        for w in data.get("staff_workers", []):
                            cur.execute(self._format_query("INSERT INTO workers (name, job, salary) VALUES (%s, %s, %s)"),
                                        (w["name"], w["job"], float(w["salary_inr"])))

                # 5. Seed Foods
                cur.execute(self._format_query("SELECT COUNT(*) FROM foods"))
                if cur.fetchone()[0] == 0:
                    creds_path = os.path.join(BASE_DIR, "users_credentials.json")
                    if os.path.exists(creds_path):
                        with open(creds_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        for fd in data.get("canteen_foods", []):
                            cur.execute(self._format_query("INSERT INTO foods (name, price, quantity) VALUES (%s, %s, %s)"),
                                        (fd["name"], float(fd["price_inr"]), int(fd["quantity"])))

                # 6. Seed Rooms
                cur.execute(self._format_query("SELECT COUNT(*) FROM rooms"))
                if cur.fetchone()[0] == 0:
                    room_seed = [
                        ("Single Deluxe", 12000.0, 10, 4, 101),
                        ("Double Sharing", 8000.0, 15, 4, 201),
                        ("Triple Sharing", 5500.0, 12, 4, 301),
                        ("Four Sharing", 4000.0, 10, 4, 401)
                    ]
                    for r in room_seed:
                        cur.execute(self._format_query("INSERT INTO rooms (type_room, price_room, total_rooms, book_rooms, starting_room) VALUES (%s, %s, %s, %s, %s)"), r)

                # 7. Seed Messages
                cur.execute(self._format_query("SELECT COUNT(*) FROM messages"))
                if cur.fetchone()[0] == 0:
                    msg_seed = [
                        ("Namaste Vikram, please ensure the room inspection for block 2 is completed today.", "owner"),
                        ("Namaste Sir, room inspection for 2nd and 3rd floor is done. Everything is in order.", "admin"),
                        ("Good. Also verify all new admissions before allocating room keys.", "owner"),
                        ("Yes Sir, all documents and fee receipts are being verified at reception.", "admin"),
                        ("Priya is managing the warden desk for student welfare and medical kits.", "admin"),
                        ("Excellent, keep me updated on the monthly mess expenditure report.", "owner")
                    ]
                    for m in msg_seed:
                        cur.execute(self._format_query("INSERT INTO messages (message, sender) VALUES (%s, %s)"), m)

                # 8. Seed Complaints
                cur.execute(self._format_query("SELECT COUNT(*) FROM complaints"))
                if cur.fetchone()[0] == 0:
                    comp_seed = [
                        ("Room 102 hot water geyser requires inspection", "Ananya Gupta"),
                        ("Wi-Fi router on 3rd floor frequent signal drops", "Ishaan Joshi"),
                        ("Study room AC filter cleaning requested", "Siddharth Malhotra")
                    ]
                    for c in comp_seed:
                        cur.execute(self._format_query("INSERT INTO complaints (complain, resident_name) VALUES (%s, %s)"), c)

                # 9. Seed Residence Requests
                cur.execute(self._format_query("SELECT COUNT(*) FROM residence_requests"))
                if cur.fetchone()[0] == 0:
                    rr_seed = [
                        ("Arjun Kapoor", "arjun.kapoor@newadmit.in", hash_pin("ak2026"), "pending"),
                        ("Kavita Sen", "kavita.sen@newadmit.in", hash_pin("ks2026"), "pending")
                    ]
                    for rr in rr_seed:
                        cur.execute(self._format_query("INSERT INTO residence_requests (name, email, pin, status) VALUES (%s, %s, %s, %s)"), rr)

                # 10. Seed Job Requests
                cur.execute(self._format_query("SELECT COUNT(*) FROM job_requests"))
                if cur.fetchone()[0] == 0:
                    jr_seed = [
                        ("Deepak Verma", "deepak.verma@work.in", "Cook Assistant"),
                        ("Santosh Patil", "santosh.patil@work.in", "Night Security Guard")
                    ]
                    for jr in jr_seed:
                        cur.execute(self._format_query("INSERT INTO job_requests (name, email, job) VALUES (%s, %s, %s)"), jr)

                # 11. Seed Ordered Foods
                cur.execute(self._format_query("SELECT COUNT(*) FROM ordered_foods"))
                if cur.fetchone()[0] == 0:
                    of_seed = [
                        ("Masala Dosa", 1, 60.0, "Aarav Mehta", "1001"),
                        ("Paneer Butter Masala", 1, 150.0, "Ananya Gupta", "1002"),
                        ("Butter Naan (2 pcs)", 1, 40.0, "Ananya Gupta", "1002"),
                        ("Special Veg Thali", 1, 120.0, "Rohan Verma", "1003"),
                        ("Chicken Biryani", 1, 180.0, "Siddharth Malhotra", "1011"),
                        ("Gulab Jamun (2 pcs)", 1, 40.0, "Aarav Mehta", "1001")
                    ]
                    for of in of_seed:
                        cur.execute(self._format_query("INSERT INTO ordered_foods (food_name, quantity, price, resident_name, pin) VALUES (%s, %s, %s, %s, %s)"), of)

                cur.close()
        finally:
            conn.close()

    def query(self, sql, params=()):
        """Execute read query and return all rows."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(self._format_query(sql), params)
            rows = cur.fetchall()
            cur.close()
            return rows
        finally:
            conn.close()

    def execute(self, sql, params=()):
        """Execute single write query with atomic commit."""
        conn = self.get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(self._format_query(sql), params)
                cur.close()
        finally:
            conn.close()

    # --- OWNER METHODS ---
    def get_owner(self, email=None, pin=None):
        """Retrieve owner. Verifies and auto-upgrades legacy password to hash if matched."""
        if email and pin:
            rows = self.query("SELECT name, email, pin, id FROM owners WHERE email = %s", (email.strip(),))
            if rows:
                r = rows[0]
                stored_pin = str(r[2])
                if verify_pin(stored_pin, pin):
                    # Auto-upgrade plain-text password to hash
                    if not stored_pin.startswith(("pbkdf2:", "scrypt:", "argon2:")):
                        self.update_owner_pin(email.strip(), pin)
                    return OwnerUser(r[0], r[1], stored_pin, id=r[3])
            return None
        elif email:
            rows = self.query("SELECT name, email, pin, id FROM owners WHERE email = %s", (email.strip(),))
        else:
            rows = self.query("SELECT name, email, pin, id FROM owners ORDER BY id ASC LIMIT 1")

        if rows:
            r = rows[0]
            return OwnerUser(r[0], r[1], str(r[2]), id=r[3])
        return None

    def update_owner_pin(self, email, new_pin):
        """Update owner password with secure hash."""
        hashed = hash_pin(new_pin)
        self.execute("UPDATE owners SET pin = %s WHERE email = %s", (hashed, email.strip()))

    def register_owner(self, name, email, pin):
        """Register a new owner with hashed password."""
        hashed = hash_pin(pin)
        self.execute("INSERT INTO owners (name, email, pin) VALUES (%s, %s, %s)", (name.strip(), email.strip(), hashed))

    # --- MANAGER METHODS ---
    def get_managers(self):
        rows = self.query("SELECT name, email, pin, id FROM managers ORDER BY id ASC")
        return [ManagerUser(r[0], r[1], str(r[2]), id=r[3]) for r in rows]

    def get_manager(self, email=None, pin=None):
        """Retrieve manager. Verifies and auto-upgrades legacy password to hash if matched."""
        if email and pin:
            rows = self.query("SELECT name, email, pin, id FROM managers WHERE email = %s", (email.strip(),))
            if rows:
                r = rows[0]
                stored_pin = str(r[2])
                if verify_pin(stored_pin, pin):
                    if not stored_pin.startswith(("pbkdf2:", "scrypt:", "argon2:")):
                        self.update_manager_pin(email.strip(), pin)
                    return ManagerUser(r[0], r[1], stored_pin, id=r[3])
            return None
        elif email:
            rows = self.query("SELECT name, email, pin, id FROM managers WHERE email = %s", (email.strip(),))
        else:
            rows = self.query("SELECT name, email, pin, id FROM managers ORDER BY id ASC LIMIT 1")

        if rows:
            r = rows[0]
            return ManagerUser(r[0], r[1], str(r[2]), id=r[3])
        return None

    def add_manager(self, name, email, pin):
        """Add a manager with hashed password. Prevents duplicates."""
        existing = self.query("SELECT id FROM managers WHERE email = %s", (email.strip(),))
        if existing:
            return False, f"Manager with email {email} already exists"
        hashed = hash_pin(pin)
        self.execute("INSERT INTO managers (name, email, pin) VALUES (%s, %s, %s)", (name.strip(), email.strip(), hashed))
        return True, "Manager added successfully"

    def update_manager_pin(self, email, new_pin):
        hashed = hash_pin(new_pin)
        self.execute("UPDATE managers SET pin = %s WHERE email = %s", (hashed, email.strip()))

    # --- RESIDENT METHODS ---
    def get_owner_dashboard_stats(self):
        """Return owner dashboard metrics without loading every resident and order."""
        conn = self.get_connection()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(expenditures), 0) FROM residents"
            )
            resident_row = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(salary), 0) FROM workers"
            )
            worker_row = cur.fetchone()
            return {
                "residents": int(resident_row[0] or 0),
                "totalincome": float(resident_row[1] or 0),
                "workers": int(worker_row[0] or 0),
                "totalsalary": float(worker_row[1] or 0),
            }
        finally:
            if cur is not None:
                cur.close()
            conn.close()

    def get_residents(self):
        rows = self.query("SELECT name, email, pin, room_no, room_type, expenditures, id FROM residents ORDER BY id ASC")
        residents = []
        for r in rows:
            person = Id(r[0], r[1], str(r[2]), r[3], r[4], r[5], id=r[6])
            order_rows = self.query("SELECT food_name, quantity, price, id FROM ordered_foods WHERE resident_name = %s", (r[0],))
            for of in order_rows:
                person.addOrderedFood(of[0], of[1], of[2])
            residents.append(person)
        return residents

    def get_resident(self, email, pin=None):
        """Retrieve resident using one bounded database connection."""
        if not email:
            return None

        clean_email = email.strip()
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                self._format_query(
                    "SELECT name, email, pin, room_no, room_type, expenditures, id "
                    "FROM residents WHERE email = %s"
                ),
                (clean_email,)
            )
            r = cur.fetchone()
            if not r:
                cur.close()
                return None

            stored_pin = str(r[2])
            if pin is not None:
                if not verify_pin(stored_pin, pin):
                    cur.close()
                    return None

                # Upgrade legacy plain-text credentials without opening a
                # second connection for the update.
                if not stored_pin.startswith(("pbkdf2:", "scrypt:", "argon2:")):
                    stored_pin = hash_pin(pin)
                    cur.execute(
                        self._format_query("UPDATE residents SET pin = %s WHERE email = %s"),
                        (stored_pin, clean_email)
                    )
                    conn.commit()

            cur.execute(
                self._format_query(
                    "SELECT food_name, quantity, price, id FROM ordered_foods "
                    "WHERE resident_name = %s"
                ),
                (r[0],)
            )
            order_rows = cur.fetchall()
            cur.close()

            person = Id(r[0], r[1], stored_pin, r[3], r[4], r[5], id=r[6])
            for of in order_rows:
                person.addOrderedFood(of[0], of[1], of[2])
            return person
        finally:
            conn.close()

    def add_resident(self, name, email, pin, room_no=0, room_type="None", expen=0):
        """Register a resident. Validates inputs, prevents duplicate email, and hashes password."""
        clean_name = name.strip()
        clean_email = email.strip()
        if not clean_name or not clean_email or not str(pin).strip():
            return False, "Name, email, and password cannot be empty."

        existing = self.query("SELECT id FROM residents WHERE email = %s", (clean_email,))
        if existing:
            return False, f"A resident with email '{clean_email}' already exists."

        hashed = hash_pin(pin)
        self.execute(
            "INSERT INTO residents (name, email, pin, room_no, room_type, expenditures) VALUES (%s, %s, %s, %s, %s, %s)",
            (clean_name, clean_email, hashed, int(room_no), room_type, float(expen))
        )
        return True, "Resident registered successfully."

    def delete_resident(self, name, pin=None, email=None):
        """Delete resident record."""
        if email:
            self.execute("DELETE FROM residents WHERE email = %s", (email.strip(),))
        elif name and pin:
            self.execute("DELETE FROM residents WHERE name = %s AND pin = %s", (name.strip(), str(pin).strip()))
        elif name:
            self.execute("DELETE FROM residents WHERE name = %s", (name.strip(),))

    def update_resident_pin(self, email, new_pin):
        hashed = hash_pin(new_pin)
        self.execute("UPDATE residents SET pin = %s WHERE email = %s", (hashed, email.strip()))

    def allot_room(self, email, pin, room_type):
        """
        Allocate a room to resident atomically:
        - Verifies resident credentials
        - Checks capacity (book_rooms < total_rooms)
        - Computes next room number
        - Updates rooms.book_rooms and resident record atomically
        Rolls back on any error.
        """
        clean_email = email.strip()
        conn = self.get_connection()
        try:
            with conn:
                cur = conn.cursor()
                # 1. Verify resident
                cur.execute(self._format_query("SELECT id, name, pin, room_no, room_type, expenditures FROM residents WHERE email = %s"), (clean_email,))
                res_row = cur.fetchone()
                if not res_row:
                    return False, "Resident account not found."

                stored_pin = str(res_row[2])
                if not verify_pin(stored_pin, pin):
                    return False, "Authentication failed. Invalid PIN."

                # 2. Check room capacity
                cur.execute(self._format_query("SELECT id, type_room, price_room, total_rooms, book_rooms, starting_room FROM rooms WHERE type_room = %s"), (room_type,))
                room_row = cur.fetchone()
                if not room_row:
                    return False, f"Room type '{room_type}' does not exist."

                price = float(room_row[2])
                total_rooms = int(room_row[3])
                book_rooms = int(room_row[4])
                starting_room = int(room_row[5])

                if book_rooms >= total_rooms:
                    return False, f"Room category '{room_type}' is fully booked ({book_rooms}/{total_rooms} rooms occupied)."

                # 3. Handle previous room category if changing room
                prev_room_type = res_row[4]
                if prev_room_type and prev_room_type != "None" and prev_room_type != room_type:
                    cur.execute(self._format_query(
                        "UPDATE rooms SET book_rooms = CASE WHEN book_rooms > 0 THEN book_rooms - 1 ELSE 0 END WHERE type_room = %s"),
                        (prev_room_type,)
                    )

                new_room_no = starting_room + book_rooms + 1
                new_book_rooms = book_rooms + 1

                # 4. Atomic updates
                cur.execute(self._format_query("UPDATE rooms SET book_rooms = %s WHERE type_room = %s"), (new_book_rooms, room_type))
                cur.execute(self._format_query("UPDATE residents SET room_no = %s, room_type = %s, expenditures = expenditures + %s WHERE email = %s"),
                            (new_room_no, room_type, price, clean_email))
                cur.close()

            return True, f"Room {new_room_no} ({room_type}) successfully allocated! Fee of ₹{price:.2f} added to expenditures."
        except Exception as e:
            return False, f"Room allocation failed: {str(e)}"
        finally:
            conn.close()

    # --- ROOM METHODS ---
    def get_rooms(self):
        rows = self.query("SELECT type_room, price_room, total_rooms, book_rooms, starting_room, id FROM rooms ORDER BY id ASC")
        return [Rooms(r[0], r[1], r[2], r[3], r[4], id=r[5]) for r in rows]

    def add_room(self, type_r, price_r, total_r, starting_r):
        type_clean = type_r.strip()
        existing = self.query("SELECT id FROM rooms WHERE type_room = %s", (type_clean,))
        if existing:
            return False, f"Room category '{type_clean}' already exists."
        self.execute("INSERT INTO rooms (type_room, price_room, total_rooms, book_rooms, starting_room) VALUES (%s, %s, %s, 0, %s)",
                     (type_clean, float(price_r), int(total_r), int(starting_r)))
        return True, "Room added successfully."

    def delete_room(self, type_r):
        self.execute("DELETE FROM rooms WHERE type_room = %s", (type_r.strip(),))

    # --- FOOD METHODS ---
    def get_foods(self):
        rows = self.query("SELECT name, price, quantity, id FROM foods ORDER BY id ASC")
        return [Foods(r[0], r[1], r[2], id=r[3]) for r in rows]

    def add_food(self, name, price, quantity):
        name_clean = name.strip()
        existing = self.query("SELECT id FROM foods WHERE name = %s", (name_clean,))
        if existing:
            # Update quantity if exists
            self.execute("UPDATE foods SET quantity = quantity + %s, price = %s WHERE name = %s", (int(quantity), float(price), name_clean))
            return True, "Food inventory updated."
        self.execute("INSERT INTO foods (name, price, quantity) VALUES (%s, %s, %s)", (name_clean, float(price), int(quantity)))
        return True, "Food added successfully."

    def delete_food(self, name):
        self.execute("DELETE FROM foods WHERE name = %s", (name.strip(),))

    def order_food(self, email, pin, food_name, quantity):
        """
        Atomic food order:
        - Validates positive quantity
        - Authenticates resident
        - Verifies stock availability
        - Deducts stock, writes frozen-price order item, and increments resident expenditure
        All in a single transaction.
        """
        try:
            qty = int(quantity)
            if qty <= 0:
                return False, "Order quantity must be at least 1."
        except (ValueError, TypeError):
            return False, "Invalid quantity specified."

        clean_email = email.strip()
        conn = self.get_connection()
        try:
            with conn:
                cur = conn.cursor()
                # 1. Authenticate resident
                cur.execute(self._format_query("SELECT id, name, pin, expenditures FROM residents WHERE email = %s"), (clean_email,))
                res_row = cur.fetchone()
                if not res_row:
                    return False, "Resident account not found."

                stored_pin = str(res_row[2])
                if not verify_pin(stored_pin, pin):
                    return False, "Authentication failed. Invalid PIN."

                res_name = res_row[1]

                # 2. Check food stock
                cur.execute(self._format_query("SELECT id, price, quantity FROM foods WHERE name = %s"), (food_name.strip(),))
                food_row = cur.fetchone()
                if not food_row:
                    return False, f"Item '{food_name}' not available on the canteen menu."

                price = float(food_row[1])
                current_stock = int(food_row[2])

                if current_stock < qty:
                    return False, f"Insufficient stock for '{food_name}'. Available: {current_stock}, Requested: {qty}."

                total_cost = price * qty
                new_stock = current_stock - qty

                # 3. Atomic deductions and order logging
                cur.execute(self._format_query("UPDATE foods SET quantity = %s WHERE name = %s"), (new_stock, food_name.strip()))
                cur.execute(self._format_query("INSERT INTO ordered_foods (food_name, quantity, price, resident_name, pin) VALUES (%s, %s, %s, %s, %s)"),
                            (food_name.strip(), qty, price, res_name, stored_pin))
                cur.execute(self._format_query("UPDATE residents SET expenditures = expenditures + %s WHERE email = %s"), (total_cost, clean_email))
                cur.close()

            return True, f"Order placed for {qty}x {food_name}! Total ₹{total_cost:.2f} billed to expenditures."
        except Exception as e:
            return False, f"Food ordering failed: {str(e)}"
        finally:
            conn.close()

    def get_ordered_foods_for_resident(self, resident_name, pin=None):
        rows = self.query("SELECT food_name, quantity, price, id FROM ordered_foods WHERE resident_name = %s ORDER BY id DESC", (resident_name.strip(),))
        return [OrderedFoods(r[0], r[1], r[2], id=r[3]) for r in rows]

    # --- WORKER METHODS ---
    def get_workers(self):
        rows = self.query("SELECT name, job, salary, id FROM workers ORDER BY id ASC")
        return [workers(r[0], r[1], r[2], id=r[3]) for r in rows]

    def add_worker(self, name, job, salary):
        self.execute("INSERT INTO workers (name, job, salary) VALUES (%s, %s, %s)",
                     (name.strip(), job.strip(), float(salary)))

    def delete_worker(self, name, job):
        self.execute("DELETE FROM workers WHERE name = %s AND job = %s", (name.strip(), job.strip()))

    # --- MESSAGING METHODS ---
    def get_messages(self):
        rows = self.query("SELECT message, sender, id FROM messages ORDER BY id ASC")
        return [f"{r[0]},{r[1]}" for r in rows]

    def add_message(self, message, sender):
        if message and message.strip():
            self.execute("INSERT INTO messages (message, sender) VALUES (%s, %s)", (message.strip(), sender.strip()))

    # --- COMPLAINTS METHODS ---
    def get_complaints(self):
        rows = self.query("SELECT complain, resident_name, id FROM complaints ORDER BY id DESC")
        return [f"{r[0]},{r[1]}" for r in rows]

    def add_complaint(self, complain, resident_name):
        if complain and complain.strip():
            self.execute("INSERT INTO complaints (complain, resident_name) VALUES (%s, %s)", (complain.strip(), resident_name.strip()))

    def get_complaints_count_for_resident(self, resident_name):
        rows = self.query("SELECT COUNT(*) FROM complaints WHERE resident_name = %s", (resident_name.strip(),))
        return rows[0][0] if rows else 0

    # --- RESIDENCE APPLICATIONS LIFECYCLE ---
    def get_residence_requests(self, status=None):
        """Get residence applications, optionally filtered by status ('pending', 'approved', 'rejected')."""
        if status:
            rows = self.query("SELECT name, email, pin, status, created_at, id FROM residence_requests WHERE status = %s ORDER BY id DESC", (status,))
        else:
            rows = self.query("SELECT name, email, pin, status, created_at, id FROM residence_requests ORDER BY id DESC")
        return [RequestResidence(r[0], r[1], str(r[2]), status=r[3], created_at=r[4], id=r[5]) for r in rows]

    def add_residence_request(self, name, email, pin):
        """Submit a public application for hostel residence with status 'pending'."""
        clean_name = name.strip()
        clean_email = email.strip()
        if not clean_name or not clean_email or not str(pin).strip():
            return False, "All fields are required."

        # Check if already a resident
        existing_res = self.query("SELECT id FROM residents WHERE email = %s", (clean_email,))
        if existing_res:
            return False, f"An active resident account already exists for {clean_email}. Please log in directly."

        # Check if already has a pending application
        pending_req = self.query("SELECT id FROM residence_requests WHERE email = %s AND status = 'pending'", (clean_email,))
        if pending_req:
            return True, "Your application is already pending review by the hostel manager."

        hashed = hash_pin(pin)
        self.execute(
            "INSERT INTO residence_requests (name, email, pin, status) VALUES (%s, %s, %s, 'pending')",
            (clean_name, clean_email, hashed)
        )
        return True, "Application submitted successfully! It is now pending manager approval."

    def approve_residence_request(self, email):
        """
        Manager approval lifecycle:
        - Atomically verifies request exists and is in 'pending' status
        - Verifies resident does not already exist
        - Transactionally creates single resident account in 'residents' table
        - Updates request status to 'approved'
        - Prevents duplicate account creation on re-approval
        """
        clean_email = email.strip()
        conn = self.get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(self._format_query("SELECT id, name, email, pin, status FROM residence_requests WHERE email = %s ORDER BY id DESC"), (clean_email,))
                req_row = cur.fetchone()
                if not req_row:
                    return False, f"Application for '{clean_email}' not found."

                req_id = req_row[0]
                name = req_row[1]
                stored_pin = str(req_row[3])
                status = req_row[4]

                if status == "approved":
                    return False, f"Application for '{clean_email}' has already been approved."

                # Verify resident account does not already exist
                cur.execute(self._format_query("SELECT id FROM residents WHERE email = %s"), (clean_email,))
                if cur.fetchone():
                    # Mark request as approved to reconcile state
                    cur.execute(self._format_query("UPDATE residence_requests SET status = 'approved' WHERE id = %s"), (req_id,))
                    return False, f"Resident account already exists for '{clean_email}'."

                # Insert resident with hashed PIN
                cur.execute(
                    self._format_query("INSERT INTO residents (name, email, pin, room_no, room_type, expenditures) VALUES (%s, %s, %s, 0, 'None', 0.0)"),
                    (name, clean_email, stored_pin)
                )

                # Update request status to approved
                cur.execute(self._format_query("UPDATE residence_requests SET status = 'approved' WHERE id = %s"), (req_id,))
                cur.close()

            return True, f"Application approved! Resident account created for {name} ({clean_email}). They can now log in at /login."
        except Exception as e:
            return False, f"Approval failed: {str(e)}"
        finally:
            conn.close()

    def reject_residence_request(self, email):
        """Mark residence application as rejected."""
        clean_email = email.strip()
        self.execute("UPDATE residence_requests SET status = 'rejected' WHERE email = %s", (clean_email,))
        return True, "Application rejected."

    def delete_residence_request(self, email):
        self.execute("DELETE FROM residence_requests WHERE email = %s", (email.strip(),))
        return True, "Application removed."

    # --- JOB REQUESTS ---
    def get_job_requests(self):
        rows = self.query("SELECT name, email, job, id FROM job_requests ORDER BY id DESC")
        return [RequestJobs(r[0], r[1], r[2], id=r[3]) for r in rows]

    def add_job_request(self, name, email, job):
        clean_name = name.strip()
        clean_email = email.strip()
        clean_job = job.strip()
        if not clean_name or not clean_email or not clean_job:
            return False, "All fields are required."
        self.execute("INSERT INTO job_requests (name, email, job) VALUES (%s, %s, %s)", (clean_name, clean_email, clean_job))
        return True, "Job application submitted successfully."

    def delete_job_request(self, email):
        self.execute("DELETE FROM job_requests WHERE email = %s", (email.strip(),))


# Authoritative singleton database instance
db = Database()
