import os
from functools import wraps
from flask import (
    Flask, render_template, request, redirect,
    session, url_for, send_from_directory, flash
)
from db import (
    db, Id, workers, Foods, Rooms,
    RequestResidence, RequestJobs, OrderedFoods,
    ManagerUser, OwnerUser
)

# Set directories
root_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(root_dir, "templates")
if not os.path.exists(template_dir):
    template_dir = os.path.join(root_dir, "templtes")
static_dir = os.path.join(root_dir, "static")

app = Flask(
    __name__,
    template_folder=template_dir,
    static_folder=static_dir,
    static_url_path="/static"
)

# Security and session configuration
app.secret_key = os.environ.get("SECRET_KEY", "hostel-management-production-key-2026-strict")
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
if os.environ.get("VERCEL"):
    app.config['SESSION_COOKIE_SECURE'] = True


# --- Authentication & Authorization Decorator ---
def login_required(role=None):
    """
    Role-based access guard.
    Redirects unauthenticated requests to their respective login portal.
    Prevents role cross-access without hardcoded identity fallback.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_role = session.get('user_role')
            if not user_role:
                if role == 'owner':
                    return redirect('/loginowner')
                elif role == 'manager':
                    return redirect('/loginmanager')
                elif role == 'resident':
                    return redirect('/login')
                return redirect('/')

            if role and user_role != role:
                # Redirect user to their own role dashboard if already authenticated as another role
                if user_role == 'owner':
                    return redirect('/ownerhome')
                elif user_role == 'manager':
                    return redirect('/managerhome')
                elif user_role == 'resident':
                    return redirect('/resident')
                return redirect('/')

            return f(*args, **kwargs)
        return decorated_function
    return decorator


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(static_dir, filename)


# Helper financial calculations
def totalexpenditures():
    residents = db.get_residents()
    return sum(float(r.expenditures) for r in residents)


def totalsalaries():
    staff = db.get_workers()
    return sum(float(w.salary) for w in staff)


# -------------------------------------------------------------
# LANDING & LOGOUT
# -------------------------------------------------------------
@app.route("/")
def Page():
    return render_template("mainLoginPage.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# -------------------------------------------------------------
# OWNER ROUTES
# -------------------------------------------------------------
@app.route("/loginowner")
def login1():
    if session.get('user_role') == 'owner':
        return redirect('/ownerhome')
    return render_template("Owner/loginOwner.html")


@app.route("/owner", methods=['POST', 'GET'])
def ownerLogin():
    if request.method == 'POST':
        pin = request.form.get('pin', '').strip()
        email = request.form.get('myemail', '').strip()
        try:
            owner_user = db.get_owner(email, pin)
        except Exception:
            app.logger.exception("Owner login database operation failed")
            return render_template(
                "Owner/loginOwner.html",
                error="Login is temporarily unavailable because the database could not be reached. Please try again shortly."
            ), 503
        if owner_user:
            session.clear()
            session['owner_email'] = owner_user.email
            session['owner_name'] = owner_user.name
            session['user_role'] = 'owner'
            return redirect('/ownerhome')
        else:
            return render_template("Owner/loginOwner.html", error="Invalid Owner credentials. Please check your email and PIN.")
    return render_template("Owner/loginOwner.html")


@app.route("/ownerhome")
@login_required(role='owner')
def owner():
    owner_email = session.get('owner_email')
    try:
        # Fetch each collection once. The previous route fetched both lists,
        # then re-fetched them inside totalexpenditures()/totalsalaries(),
        # multiplying database round trips on every owner dashboard load.
        residents = db.get_residents()
        staff = db.get_workers()
        total_income = sum(float(getattr(r, "expenditures", 0) or 0) for r in residents)
        total_salary = sum(float(getattr(w, "salary", 0) or 0) for w in staff)

        return render_template(
            "Owner/owner.html",
            variable=owner_email,
            residents=len(residents),
            workers=len(staff),
            totalincome=total_income,
            totalsalary=total_salary
        )
    except Exception:
        app.logger.exception("Owner dashboard data loading failed")
        return (
            "The owner dashboard could not load its data. Please refresh and try again.",
            503
        )


@app.route("/createAccountManager")
@login_required(role='owner')
def createManager():
    owner_email = session.get('owner_email')
    return render_template("Owner/registerManager.html", variable=owner_email)


@app.route("/signupmanager", methods=['POST', 'GET'])
@login_required(role='owner')
def signupmgr():
    owner_email = session.get('owner_email')
    if request.method == 'POST':
        name = (request.form.get('name1', '') + " " + request.form.get('name2', '')).strip()
        email = request.form.get('email', '').strip()
        pin = request.form.get('password', '').strip()
        success, msg = db.add_manager(name, email, pin)
        if not success:
            return render_template("Owner/registerManager.html", variable=owner_email, error=msg)
        return redirect('/ownerhome')
    return render_template("Owner/registerManager.html", variable=owner_email)


@app.route("/sendMessage")
@login_required(role='owner')
def msgOwner():
    owner_email = session.get('owner_email')
    return render_template("Owner/sendMessageToManager.html", variable=owner_email)


@app.route("/sendMessageByOwner", methods=['POST', 'GET'])
@login_required(role='owner')
def smsowner():
    owner_email = session.get('owner_email')
    if request.method == 'POST':
        sms = request.form.get('sms', '').strip()
        if sms:
            db.add_message(sms, "owner")
            return redirect('/recieveMessage')
    return render_template("Owner/sendMessageToManager.html", variable=owner_email)


@app.route("/recieveMessage")
@app.route("/receiveMessage")
@login_required(role='owner')
def recMessage():
    owner_email = session.get('owner_email')
    raw_msgs = db.get_messages()
    messages = []
    for m in raw_msgs:
        parts = m.split(",")
        if len(parts) >= 2:
            messages.append({"text": parts[0], "sender": parts[1]})
        else:
            messages.append({"text": m, "sender": "user"})
    return render_template("Owner/recieveMessageOwner.html", variable=owner_email, messages=messages)


@app.route("/residentsDetail")
@login_required(role='owner')
def ownerResidents():
    owner_email = session.get('owner_email')
    residents = db.get_residents()
    return render_template("Owner/residentDetail.html", variable=owner_email, MyList=residents, My_List=residents, myList=residents)


@app.route("/workersDetail")
@login_required(role='owner')
def ownerWorkers():
    owner_email = session.get('owner_email')
    staff = db.get_workers()
    return render_template("Owner/workersDetail.html", variable=owner_email, MyList=staff, My_List=staff, myList=staff)


@app.route("/room")
@login_required(role='owner')
def ownerRooms():
    owner_email = session.get('owner_email')
    rooms = db.get_rooms()
    return render_template("Owner/roomsDetail.html", variable=owner_email, MyList=rooms, My_List=rooms, myList=rooms)


@app.route("/profileowner")
@login_required(role='owner')
def profile1():
    owner_email = session.get('owner_email')
    owner_user = db.get_owner(owner_email)
    if not owner_user:
        owner_user = db.get_owner()
    return render_template("Owner/profileOwner.html", email=owner_user.email, name=owner_user.name, pin="••••••")


@app.route("/profilemanagerOwner")
@login_required(role='owner')
def profile2():
    managers = db.get_managers()
    mgr = managers[0] if managers else ManagerUser("Vikram Patel", "vikram.patel@hostel.in", "1111")
    return render_template("Owner/profileManager.html", email=mgr.email, name=mgr.name, pin="••••••")


@app.route("/registerMyOwner")
def regOwner():
    return render_template("Owner/registerOwner.html")


@app.route("/myregisterOwner", methods=['POST', 'GET'])
def myregOwner():
    if request.method == 'POST':
        name = (request.form.get('name1', '') + " " + request.form.get('name2', '')).strip()
        email = request.form.get('email', '').strip()
        pin = request.form.get('password', '').strip()
        db.register_owner(name, email, pin)
        return redirect("/loginowner")
    return render_template("Owner/registerOwner.html")


@app.route("/forgotpinowner")
def forgotpinowner():
    return render_template("Owner/forgotpinowner.html")


@app.route("/changepasswordowner")
@login_required(role='owner')
def changePassOwner():
    owner_email = session.get('owner_email')
    return render_template("Owner/changeownerpin.html", variable=owner_email)


@app.route("/changePinMyOwner", methods=['POST', 'GET'])
@login_required(role='owner')
def changePinOwner():
    owner_email = session.get('owner_email')
    if request.method == 'POST':
        old = request.form.get('oldpin', '').strip()
        new = request.form.get('newpin', '').strip()
        owner_user = db.get_owner(owner_email, old)
        if owner_user:
            db.update_owner_pin(owner_email, new)
            return redirect("/loginowner")
        else:
            return render_template("Owner/changeownerpin.html", variable=owner_email, error="Current PIN is incorrect.")
    return render_template("Owner/changeownerpin.html", variable=owner_email)


@app.route("/frogotPinOwner", methods=['POST', 'GET'])
def forgotPinOwnerAction():
    if request.method == 'POST':
        email = request.form.get('myemail', '').strip()
        new_pin = request.form.get('pin', '').strip()
        owner_user = db.get_owner(email)
        if owner_user:
            db.update_owner_pin(email, new_pin)
            return redirect("/loginowner")
        return render_template("Owner/forgotpinowner.html", error="Owner email address not found.")
    return render_template("Owner/forgotpinowner.html")


# -------------------------------------------------------------
# MANAGER ROUTES
# -------------------------------------------------------------
@app.route("/loginmanager")
def login2():
    if session.get('user_role') == 'manager':
        return redirect('/managerhome')
    return render_template("Manager/loginManager.html")


@app.route("/manager", methods=['POST', 'GET'])
def managerLogin():
    if request.method == 'POST':
        pin = request.form.get('pin', '').strip()
        email = request.form.get('myemail', '').strip()
        try:
            mgr = db.get_manager(email, pin)
        except Exception:
            app.logger.exception("Manager login database operation failed")
            return render_template(
                "Manager/loginManager.html",
                error="Login is temporarily unavailable because the database could not be reached. Please try again shortly."
            ), 503
        if mgr:
            session.clear()
            session['manager_email'] = mgr.email
            session['manager_name'] = mgr.name
            session['user_role'] = 'manager'
            return redirect('/managerhome')
        else:
            return render_template("Manager/loginManager.html", error="Invalid Manager credentials. Please check your email and PIN.")
    return render_template("Manager/loginManager.html")


@app.route("/managerhome")
@login_required(role='manager')
def managerHome():
    email = session.get('manager_email')
    residents = db.get_residents()
    staff = db.get_workers()
    foods = db.get_foods()
    rooms = db.get_rooms()
    return render_template(
        "Manager/manager.html",
        variable=email,
        totalresidents=len(residents),
        totalworkers=len(staff),
        totalfoods=len(foods),
        types=len(rooms)
    )


@app.route("/profilemanager")
@login_required(role='manager')
def profileMgr():
    email = session.get('manager_email')
    mgr = db.get_manager(email)
    if not mgr:
        mgr = ManagerUser("Vikram Patel", email or "vikram.patel@hostel.in", "1111")
    return render_template("Manager/profileManager.html", email=mgr.email, name=mgr.name, pin="••••••")


@app.route("/sendMessageManager")
@login_required(role='manager')
def sendMsgMgr():
    email = session.get('manager_email')
    return render_template("Manager/sendMessageToOwner.html", variable=email)


@app.route("/sendMessageByManager", methods=['POST', 'GET'])
@login_required(role='manager')
def sendMsgByMgr():
    email = session.get('manager_email')
    if request.method == 'POST':
        sms = request.form.get('sms', '').strip()
        if sms:
            db.add_message(sms, "admin")
            return redirect('/recieveMessageManager')
    return render_template("Manager/sendMessageToOwner.html", variable=email)


@app.route("/recieveMessageManager")
@app.route("/receiveMessageManager")
@login_required(role='manager')
def recMsgMgr():
    email = session.get('manager_email')
    raw_msgs = db.get_messages()
    messages = []
    for m in raw_msgs:
        parts = m.split(",")
        if len(parts) >= 2:
            messages.append({"text": parts[0], "sender": parts[1]})
        else:
            messages.append({"text": m, "sender": "user"})
    return render_template("Manager/recieveMessageManager.html", variable=email, messages=messages)


@app.route("/createAccountresident")
@login_required(role='manager')
def createResidentPage():
    return render_template("Manager/registerResident.html")


@app.route("/registerResident", methods=['POST', 'GET'])
@login_required(role='manager')
def registerResident():
    if request.method == 'POST':
        name = (request.form.get('name1', '') + " " + request.form.get('name2', '')).strip()
        email = request.form.get('email', '').strip()
        pin = request.form.get('password', '').strip()
        success, msg = db.add_resident(name, email, pin)
        if not success:
            return render_template("Manager/registerResident.html", error=msg)
        return redirect("/residents")
    return render_template("Manager/registerResident.html")


@app.route("/addWorker")
@login_required(role='manager')
def addWorkerPage():
    return render_template("Manager/WorkerAdd.html")


@app.route("/addworker", methods=['POST', 'GET'])
@login_required(role='manager')
def addWorkerAction():
    if request.method == 'POST':
        name = (request.form.get('name1', '') + " " + request.form.get('name2', '')).strip()
        job = request.form.get('job', '').strip()
        salary = request.form.get('salary', 0)
        db.add_worker(name, job, salary)
        return redirect("/workers")
    return render_template("Manager/WorkerAdd.html")


@app.route("/addFood")
@login_required(role='manager')
def addFoodPage():
    return render_template("Manager/foodAdd.html")


@app.route("/addFastFood", methods=['POST', 'GET'])
@login_required(role='manager')
def addFoodAction():
    if request.method == 'POST':
        name = (request.form.get('name1', '') + " " + request.form.get('name2', '')).strip()
        quantity = request.form.get('quantity', 0)
        price = request.form.get('price', 0)
        db.add_food(name, price, quantity)
        return redirect("/foods")
    return render_template("Manager/foodAdd.html")


@app.route("/residents")
@login_required(role='manager')
def managerResidents():
    email = session.get('manager_email')
    residents = db.get_residents()
    return render_template("Manager/residentDetail.html", variable=email, MyList=residents, My_List=residents, myList=residents)


@app.route("/deleteResident", methods=['POST', 'GET'])
@login_required(role='manager')
def deleteResident():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        pin = request.form.get('pin', '').strip()
        email = request.form.get('email', '').strip()
        db.delete_resident(name, pin, email)
    return redirect("/residents")


@app.route("/workers")
@login_required(role='manager')
def managerWorkers():
    email = session.get('manager_email')
    staff = db.get_workers()
    return render_template("Manager/workersDetail.html", variable=email, MyList=staff, My_List=staff, myList=staff)


@app.route("/deleteWorker", methods=['POST', 'GET'])
@login_required(role='manager')
def deleteWorker():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        job = request.form.get('job', '').strip()
        if name and job:
            db.delete_worker(name, job)
    return redirect("/workers")


@app.route("/Rooms")
@login_required(role='manager')
def managerRooms():
    email = session.get('manager_email')
    rooms = db.get_rooms()
    return render_template("Manager/roomsDetail.html", variable=email, MyList=rooms, My_List=rooms, myList=rooms)


@app.route("/deleteRoom", methods=['POST', 'GET'])
@login_required(role='manager')
def deleteRoom():
    if request.method == 'POST':
        type_r = request.form.get('typeroom', '').strip()
        if type_r:
            db.delete_room(type_r)
    return redirect("/Rooms")


@app.route("/foods")
@login_required(role='manager')
def managerFoods():
    email = session.get('manager_email')
    foods = db.get_foods()
    return render_template("Manager/foodDetail.html", variable=email, MyList=foods, My_List=foods, myList=foods)


@app.route("/deleteFood", methods=['POST', 'GET'])
@login_required(role='manager')
def deleteFood():
    if request.method == 'POST':
        name = request.form.get('foodname', '').strip()
        if name:
            db.delete_food(name)
    return redirect("/foods")


@app.route("/complains")
@app.route("/complaints")
@login_required(role='manager')
def managerComplaints():
    email = session.get('manager_email')
    raw_comp = db.get_complaints()
    complaints = []
    for c in raw_comp:
        parts = c.split(",")
        if len(parts) >= 2:
            complaints.append({"text": parts[0], "sender": parts[1]})
        else:
            complaints.append({"text": c, "sender": "Student"})
    return render_template("Manager/recievedComplains.html", variable=email, complains=complaints)


# --- Residence Applications Lifecycle (Submit -> Review -> Approve/Reject) ---
@app.route("/requestsResidents")
@login_required(role='manager')
def managerResidenceReqs():
    email = session.get('manager_email')
    requests = db.get_residence_requests()
    return render_template("Manager/requestResidence.html", variable=email, MyList=requests)


@app.route("/approveResidenceRequest", methods=['POST'])
@login_required(role='manager')
def approveResidenceAction():
    email_mgr = session.get('manager_email')
    email = request.form.get('email', '').strip()
    success, msg = db.approve_residence_request(email)
    requests = db.get_residence_requests()
    if success:
        return render_template("Manager/requestResidence.html", variable=email_mgr, MyList=requests, success=msg)
    else:
        return render_template("Manager/requestResidence.html", variable=email_mgr, MyList=requests, error=msg)


@app.route("/deleteResidentapply", methods=['POST', 'GET'])
@login_required(role='manager')
def deleteResidentApply():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        if email:
            db.delete_residence_request(email)
    return redirect("/requestsResidents")


@app.route("/requestsJobs")
@login_required(role='manager')
def managerJobReqs():
    email = session.get('manager_email')
    requests = db.get_job_requests()
    return render_template("Manager/requestsJobs.html", variable=email, MyList=requests)


@app.route("/deleteJob", methods=['POST', 'GET'])
@login_required(role='manager')
def deleteJobAction():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        if email:
            db.delete_job_request(email)
    return redirect("/requestsJobs")


@app.route("/changinRoom")
@app.route("/changingRoom")
@login_required(role='manager')
def addRoomPage():
    return render_template("Manager/Rooms.html")


@app.route("/addanewmyRoom", methods=['POST', 'GET'])
@login_required(role='manager')
def addNewRoom():
    if request.method == "POST":
        type_r = request.form.get('typeR', '').strip()
        price_r = request.form.get('priceR', 0)
        total_r = request.form.get('totalR', 0)
        starting_r = request.form.get('startingR', 0)
        db.add_room(type_r, price_r, total_r, starting_r)
        return redirect("/Rooms")
    return render_template("Manager/Rooms.html")


@app.route("/changepasswordmanager")
@login_required(role='manager')
def changePassMgr():
    email = session.get('manager_email')
    return render_template("Manager/changemanagerpin.html", variable=email)


@app.route("/forgotmanagerpin")
def forgotPinMgr():
    return render_template("Manager/forgotpinmanager.html")


@app.route("/changePinMyManager", methods=['POST', 'GET'])
@login_required(role='manager')
def changePinMgrAction():
    email = session.get('manager_email')
    if request.method == 'POST':
        old = request.form.get('oldpin', '').strip()
        new = request.form.get('newpin', '').strip()
        mgr = db.get_manager(email, old)
        if mgr:
            db.update_manager_pin(email, new)
            return redirect("/loginmanager")
        return render_template("Manager/changemanagerpin.html", variable=email, error="Current PIN is incorrect.")
    return render_template("Manager/changemanagerpin.html", variable=email)


@app.route("/frogotPinManager", methods=['POST', 'GET'])
def forgotPinMgrAction():
    if request.method == 'POST':
        email = request.form.get('myemail', '').strip()
        new_pin = request.form.get('pin', '').strip()
        mgr = db.get_manager(email)
        if mgr:
            db.update_manager_pin(email, new_pin)
            return redirect("/loginmanager")
        return render_template("Manager/forgotpinmanager.html", error="Manager email address not found.")
    return render_template("Manager/forgotpinmanager.html")


# -------------------------------------------------------------
# RESIDENT ROUTES
# -------------------------------------------------------------
@app.route("/login")
@app.route("/loginresident")
@app.route("/loginResident")
def residentLoginPage():
    if session.get('user_role') == 'resident':
        return redirect('/resident')
    return render_template("Resident/loginResident.html")


@app.route("/success", methods=['POST', 'GET'])
def residentLogin():
    if request.method == 'POST':
        pin = request.form.get('pin', '').strip()
        email = request.form.get('myemail', '').strip()
        try:
            resident = db.get_resident(email, pin)
        except Exception:
            app.logger.exception("Resident login database operation failed")
            return render_template(
                "Resident/loginResident.html",
                error="Login is temporarily unavailable because the database could not be reached. Please try again shortly."
            ), 503
        if resident:
            session.clear()
            session['resident_email'] = resident.email
            session['resident_name'] = resident.name
            session['user_role'] = 'resident'
            return redirect('/resident')
        else:
            return render_template(
                "Resident/loginResident.html",
                error="Invalid email or password. If you applied for hostel residence recently, please wait for manager approval before signing in."
            )
    return render_template("Resident/loginResident.html")


@app.route("/resident")
@login_required(role='resident')
def residentDashboard():
    email = session.get('resident_email')
    resident = db.get_resident(email)
    if not resident:
        return redirect('/login')
    complaint_count = db.get_complaints_count_for_resident(resident.name)
    return render_template(
        "Resident/resident.html",
        variable=resident.email,
        roomtype=resident.roomType,
        roomno=resident.roomNo,
        expen=resident.expenditures,
        yourcomplains=complaint_count
    )


@app.route("/profileresident")
@login_required(role='resident')
def profileResident():
    email = session.get('resident_email')
    resident = db.get_resident(email)
    if not resident:
        return redirect('/login')
    return render_template("Resident/profileResident.html", email=resident.email, name=resident.name, pin="••••••")


@app.route("/getRoom")
@login_required(role='resident')
def residentGetRoom():
    email = session.get('resident_email')
    rooms = db.get_rooms()
    return render_template("Resident/allotmentofRoom.html", variable=email, roomlist=rooms)


@app.route("/getnewRoom", methods=['POST', 'GET'])
@login_required(role='resident')
def residentAllotRoom():
    email = session.get('resident_email')
    rooms = db.get_rooms()
    if request.method == 'POST':
        pin = request.form.get('pin', '').strip()
        room_type = request.form.get('room', '').strip()
        success, msg = db.allot_room(email, pin, room_type)
        if success:
            return redirect('/resident')
        return render_template("Resident/allotmentofRoom.html", variable=email, roomlist=rooms, error=msg)
    return render_template("Resident/allotmentofRoom.html", variable=email, roomlist=rooms)


@app.route("/orderfood")
@login_required(role='resident')
def residentOrderFoodPage():
    email = session.get('resident_email')
    foods = db.get_foods()
    return render_template("Resident/orderFood.html", variable=email, foodList=foods)


@app.route("/orderfoodnow", methods=['POST', 'GET'])
@login_required(role='resident')
def residentOrderFoodAction():
    email = session.get('resident_email')
    foods = db.get_foods()
    if request.method == 'POST':
        pin = request.form.get('pin', '').strip()
        food_name = request.form.get('food', '').strip()
        quantity = request.form.get('quantity', 1)
        success, msg = db.order_food(email, pin, food_name, quantity)
        if success:
            return redirect('/orderfooddetail')
        return render_template("Resident/orderFood.html", variable=email, foodList=foods, error=msg)
    return render_template("Resident/orderFood.html", variable=email, foodList=foods)


@app.route("/orderfooddetail")
@login_required(role='resident')
def residentOrderFoodDetail():
    email = session.get('resident_email')
    resident = db.get_resident(email)
    if not resident:
        return redirect('/login')
    orders = db.get_ordered_foods_for_resident(resident.name)
    return render_template("Resident/orderedfoods.html", variable=resident.email, foodList=orders)


@app.route("/foodDetail")
@login_required(role='resident')
def residentFoodDetail():
    email = session.get('resident_email')
    foods = db.get_foods()
    return render_template("Resident/foodDetails.html", variable=email, MyList=foods)


@app.route("/sendComplains")
@app.route("/sendComplaints")
@login_required(role='resident')
def residentSendComplaints():
    email = session.get('resident_email')
    return render_template("Resident/sendComplains.html", variable=email)


@app.route("/sendComplain", methods=['POST', 'GET'])
@app.route("/sendComplaint", methods=['POST', 'GET'])
@login_required(role='resident')
def residentSendComplaintAction():
    email = session.get('resident_email')
    resident = db.get_resident(email)
    name = resident.name if resident else "Resident"
    if request.method == 'POST':
        sms = request.form.get('sms', '').strip()
        if sms:
            db.add_complaint(sms, name)
            return redirect('/resident')
    return render_template("Resident/sendComplains.html", variable=email)


@app.route("/forgotpinresident")
def residentForgotPin():
    return render_template("Resident/changeresidentpin.html", variable="")


@app.route("/changepinresident")
@login_required(role='resident')
def residentChangePin():
    email = session.get('resident_email')
    return render_template("Resident/forgot-pin-resident.html", variable=email)


@app.route("/changePinMyResident", methods=['POST', 'GET'])
@login_required(role='resident')
def changePinResidentAction():
    email = session.get('resident_email')
    if request.method == 'POST':
        old = request.form.get('oldpin', '').strip()
        new = request.form.get('newpin', '').strip()
        resident = db.get_resident(email, old)
        if resident:
            db.update_resident_pin(email, new)
            return redirect('/login')
        return render_template("Resident/changeresidentpin.html", variable=email, error="Current PIN is incorrect.")
    return render_template("Resident/changeresidentpin.html", variable=email)


@app.route("/frogotPinResident", methods=['POST', 'GET'])
def forgotPinResidentAction():
    if request.method == 'POST':
        email = request.form.get('myemail', '').strip()
        new_pin = request.form.get('pin', '').strip()
        resident = db.get_resident(email)
        if resident:
            db.update_resident_pin(email, new_pin)
            return redirect("/login")
        return render_template("Resident/forgot-pin-resident.html", error="Resident email address not found.")
    return render_template("Resident/forgot-pin-resident.html")


# -------------------------------------------------------------
# PUBLIC / APPLICANT ROUTES
# -------------------------------------------------------------
@app.route("/peoplehome")
def publicHome():
    return render_template("people.html")


@app.route("/applyresidence")
def publicApplyResidence():
    return render_template("ResidenceApply.html")


@app.route("/applyjob")
def publicApplyJob():
    return render_template("ApplyJob.html")


@app.route("/applyResidence", methods=['POST', 'GET'])
def publicApplyResidenceAction():
    if request.method == "POST":
        name = (request.form.get('name1', '') + " " + request.form.get('name2', '')).strip()
        email = request.form.get('email', '').strip()
        pin = request.form.get('pin', '').strip()
        success, msg = db.add_residence_request(name, email, pin)
        if success:
            return render_template("ResidenceApply.html", success=msg)
        return render_template("ResidenceApply.html", error=msg)
    return render_template("ResidenceApply.html")


@app.route("/applyJob", methods=['POST', 'GET'])
def publicApplyJobAction():
    if request.method == "POST":
        name = (request.form.get('name1', '') + " " + request.form.get('name2', '')).strip()
        email = request.form.get('email', '').strip()
        job = request.form.get('job', '').strip()
        success, msg = db.add_job_request(name, email, job)
        if success:
            return render_template("ApplyJob.html", success=msg)
        return render_template("ApplyJob.html", error=msg)
    return render_template("ApplyJob.html")


# -------------------------------------------------------------
# VERCEL SERVERLESS & LOCAL ENTRYPOINT
# -------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
