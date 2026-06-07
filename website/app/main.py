import os
import re
import json
import hashlib
import random
import requests
import threading
from flask import Flask, render_template, request, redirect, session, jsonify
import psycopg2

from app import config
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0 #############WARNING: remove in production
app.secret_key = config.appSecretKey

def get_max_dob():
    return (datetime.now() - timedelta(days=18*365.25)).strftime('%Y-%m-%d')

BLOCKCHAIN_SERVERS = os.environ.get('BLOCKCHAIN_URL', 'http://127.0.0.1:5000').split(',')

import sqlite3

class SQLiteCursorWrapper:
    def __init__(self, cursor, conn):
        self.cursor = cursor
        self.conn = conn

    def execute(self, query, params=None):
        # Convert %s to ?
        query = query.replace('%s', '?')
        # Replace MySQL style double quoted variables in raw sql formatting (like in line 186)
        if params is None:
            query = query.replace('"{dob}"', "'{dob}'")
            query = query.replace('"{key}"', "'{key}'")
        else:
            query = query.replace('"{dob}"', "?")
            query = query.replace('"{key}"', "?")

        if params is not None:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)
        self.conn.commit()

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        return tuple(row)

    def fetchall(self):
        rows = self.cursor.fetchall()
        return [tuple(row) for row in rows]

class SQLiteConnectionWrapper:
    def __init__(self, db_path="e_voting.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.autocommit = True

    def cursor(self):
        return SQLiteCursorWrapper(self.conn.cursor(), self.conn)

    def rollback(self):
        self.conn.rollback()

def init_sqlite_db(db_path="e_voting.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS voter_list (
        voter_id INTEGER PRIMARY KEY,
        name TEXT,
        password_hash TEXT,
        aadhar_id TEXT,
        voter_card TEXT DEFAULT '',
        dob TEXT,
        email TEXT DEFAULT '',
        contact_no TEXT,
        key_hash TEXT,
        voted INTEGER DEFAULT 0,
        verified INTEGER DEFAULT 0
    )
    """)
    try:
        cursor.execute("ALTER TABLE voter_list ADD COLUMN voter_card TEXT DEFAULT ''")
    except:
        pass
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS candidate_list (
        candidate_id INTEGER PRIMARY KEY,
        name TEXT,
        party TEXT
    )
    """)
    cursor.execute("SELECT count(*) FROM candidate_list")
    if cursor.fetchone()[0] == 0:
        candidates = [
            (100001, "Candidate A", "Party Blue"),
            (100002, "Candidate B", "Party Orange"),
            (100003, "Candidate C", "Party Green")
        ]
        cursor.executemany("INSERT INTO candidate_list VALUES (?, ?, ?)", candidates)
    conn.commit()
    conn.close()

USING_SQLITE = False

DATABASE_URL = ''
if 'DATABASE_URL' in dir(config):
    DATABASE_URL = config.DATABASE_URL
else:
    DATABASE_URL = os.environ.get('DATABASE_URL', '')

try:
    if not DATABASE_URL or "username" in DATABASE_URL or "localhost" in DATABASE_URL:
        raise Exception("Database URL is empty or placeholder")
    connection = psycopg2.connect(DATABASE_URL, sslmode = 'require')
    connection.autocommit = True
    cursor = connection.cursor()
except Exception as e:
    print(f"PostgreSQL connection failed ({e}). Falling back to SQLite local database...")
    init_sqlite_db()
    connection = SQLiteConnectionWrapper()
    cursor = connection.cursor()
    USING_SQLITE = True


@app.route('/')
def index():
    return render_template('index.html', loggedin = is_loggedin())

@app.route('/healthz')
def healthz():
    return 'OK'


@app.route('/dashboard')
def dashboard():
    if is_loggedin():
        voter_id = session['voter_id']
        try:
            query = "select aadhar_id, voter_card, dob, contact_no, email, voted from voter_list where voter_id = %s"
            cursor.execute(query, (voter_id, ))
            res = cursor.fetchone()
            aadhar_id = res[0]
            voter_card = res[1]
            dob = res[2]
            contact_no = res[3]
            email = res[4]
            voted = False if res[5] == 0 else True
            return render_template(
                "dashboard.html",
                loggedin = True,
                username = session['name'],
                voter_id = voter_id,
                aadhar_id = aadhar_id,
                voter_card = voter_card,
                dob = dob,
                contact_no = contact_no,
                email = email,
                voted = voted
            )
        except Exception as e:
            print(str(e))
            return logout()

    else:
        return redirect("/")


@app.route('/register', methods=['POST', 'GET'])
def register():
    maxDate = get_max_dob()
    if request.method == 'GET':
        if is_loggedin():
            return redirect('/')
        return render_template('register.html', loggedin = False, maxDate = maxDate)
    else:
        aadhar_id = request.form['aadhar_id']
        data = dict(request.form)
        response = create_user(data)
        if 'error' in response:
            return render_template('register.html', error=response['error'], loggedin = False, maxDate = maxDate)
        key = response['key']
        cursor.execute("select voter_id, name from voter_list where aadhar_id = %s", (aadhar_id, ))
        res = cursor.fetchone()
        voter_id = res[0]
        name = res[1]
        login(name, voter_id)
        return render_template('key.html', voter_id = voter_id, key=key, loggedin = is_loggedin())


@app.route('/results')
def results():
    return render_template('results.html', loggedin = is_loggedin())


@app.route('/login', methods=['POST', 'GET'])
def login_route():
    if request.method == 'POST':
        check_mysql_connection(cursor)
        try:
            name = request.form['name']
            name = ' '.join([word.capitalize() for word in name.split()])
            voter_card = re.sub(r'[^A-Za-z0-9]', '', request.form['voter_card']).upper()
            password = request.form['password']
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            cursor.execute("select name, voter_id, password_hash from voter_list where voter_card = %s;", (voter_card, ))
            result = cursor.fetchone()
            if result is not None:
                if name == result[0] and password_hash == result[2]:
                    login(name, result[1])
                    return redirect('/')
                else:
                    return render_template('login.html', warning="User name or password does not match. Try again.")
            else:
                return render_template('login.html', warning="Invalid Voter Card Number.")
        except Exception as e:
            print(str(e))
            return render_template('error.html', error="Unable to connect to the database. Please try again later.")
    else:
        if is_loggedin():
            return redirect('/')
        return render_template('login.html', loggedin = False)


@app.route('/cast', methods=['GET', 'POST'])
def cast():
    if is_loggedin():
        candidateList = get_candidate_list()
        return render_template('cast.html', candidateList=candidateList, loggedin = True, username = session['name'], voter_id = session['voter_id'], blockchain_servers = BLOCKCHAIN_SERVERS)
    else:
        return redirect('/login')


@app.route('/candidate_list')
def candidate_list():
    candidateList = get_candidate_list()
    return render_template('candidates.html', candidateList = candidateList, loggedin = is_loggedin())


@app.route('/voter_list')
def voter_list():
    query = "select voter_id, name, voted from voter_list order by voter_id;"
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
    except:
        check_mysql_connection(cursor)
        try:
            cursor.execute(query)
            rows = cursor.fetchall()
        except Exception as e:
            print(str(e))
            return render_template('error.html', error="Error in fetching data from database.")
    voterList = []
    for row in rows:
        voterList.append({
            'voter_id': row[0],
            'name': row[1],
            'voted': row[2]
        })
    return render_template('voters.html', voterList=voterList, loggedin = is_loggedin())


@app.route('/logout')
def logout():
    session.pop('name', None)
    session.pop('voter_id', None)
    return redirect('/')


#################################################*Andriod App API Routes*#############################################

@app.route('/create_user', methods=['POST'])
def create_user_route():
    try:
        name = request.form['name']
        password = request.form['password']
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        aadhar_id = request.form['aadhar_id']
        voter_card = re.sub(r'[^A-Za-z0-9]', '', request.form.get('voter_card', '')).upper()
        dob = request.form['dob']
        contact_no = request.form['contact_no']
        lst = [name, aadhar_id, dob, contact_no, random.randrange(10**10)]
        key = hashlib.md5(str(lst).encode()).hexdigest()
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        voter_id = int(aadhar_id[-5:]+ contact_no[-3:])
        cursor.execute("insert into voter_list (voter_id, name, password_hash, aadhar_id, voter_card, dob, email, contact_no, key_hash, voted, verified) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 1);", (
            voter_id, name, password_hash, aadhar_id, voter_card, dob, '', contact_no, key_hash
        ))
        return key
    except Exception as e:
        connection.rollback()
        print('Error: ', e)
    return '0'

@app.route('/get_candidates')
def get_candidates():
    candidateList = get_candidate_list()
    return jsonify(candidateList)

######################################################*Common*###########################################################

@app.route('/api/get_result')
def get_result_api():
    return jsonify(get_results())


@app.route('/api/update_key', methods=['POST'])
def update_key():
    voter_id = request.form['voter_id']
    password = request.form['password']
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    check_mysql_connection(cursor)
    query = "select name, aadhar_id, dob, contact_no, password_hash from voter_list where voter_id = %s;"
    cursor.execute(query, (voter_id, ))
    row = cursor.fetchone()
    if row is None:
        return ''
    if password_hash == row[4]:
        name = row[0]
        aadhar_id = row[1]
        dob = row[2]
        contact_no = row[3]
        lst = [name, aadhar_id, dob, contact_no, random.randrange(10**10)]
        key = hashlib.md5(str(lst).encode()).hexdigest()
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        cursor.execute("update voter_list set key_hash = %s where voter_id = %s;", (key_hash, voter_id))
        return key
    return ''


############################################*Blockchain server API routes*###############################################

@app.route('/api/voter_check', methods=['POST'])
def api_voter_check():
    voter_id = request.form['voter_id']
    key_hash = request.form['key_hash']
    error = ""
    query = "select key_hash, voted, verified from voter_list where voter_id = %s;"
    try:
        cursor.execute(query, (voter_id, ))
        result = cursor.fetchone()
    except:
        check_mysql_connection(cursor)
        try:
            cursor.execute(query, (voter_id, ))
            result = cursor.fetchone()
        except Exception as e:
            print(str(e))
            return {
                "status": 0,
                "error": "Unable to connect to the database"
            }

    if result is None:
        error = "Invalid Voter ID"
    else:
        voted = result[1]
        verified = result[2]
        if result[0] == key_hash:
            if verified == 1:
                if voted < len(BLOCKCHAIN_SERVERS):
                    cursor.execute("update voter_list set voted = voted + 1 where voter_id = %s", (voter_id, ))
                    return {"status": 1}
                else:
                    error = "Already Voted"
            else:
                error = "Voter ID not verified"
        else:
            error = "Incorrect Key"

    return {
        "status": 0,
        "error": error
    }

##############################################*Helper Functions*###########################################################

def create_user(data : dict):
    check_mysql_connection(cursor)
    error_msg = ''
    try:
        name = data['name']
        name = ' '.join([word.capitalize() for word in name.split()])
        password = data['password']
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        aadhar_id = data['aadhar_id']
        voter_card = re.sub(r'[^A-Za-z0-9]', '', data.get('voter_card', '')).upper()
        dob = data['dob']
        contact_no = data['contact_no']
        email = data['email']
        verified = True               #verification automated

        if re.search('[a-zA-Z]', name) is None:
            error_msg = 'Invalid name'
        elif dob == '':
            error_msg = 'Invalid Date of Birth'
        elif dob > get_max_dob():
            error_msg = 'You must be 18 years or older to register'
        elif re.search('^[1-9]{1}[0-9]{11}$', aadhar_id) is None:
            error_msg = 'Invalid Aadhar ID'
        elif re.search('^[A-Z]{3}[0-9]{7}$', voter_card) is None:
            error_msg = 'Invalid Voter Card Number (format: XXX1234567)'
        elif re.search('^[1-9]{1}[0-9]{9}$', contact_no) is None:
            error_msg = 'Invalid Contact Number'
        elif re.search("[^@]+@[^@]+\\.[^@]+", email) is None:
            error_msg = 'Invalid Email ID'
        else:
            lst = [name, aadhar_id, dob, contact_no, random.randrange(10**10)]
            key = hashlib.md5(str(lst).encode()).hexdigest()
            key_hash = hashlib.sha256(key.encode()).hexdigest()
            cursor.execute("select voter_id from voter_list where aadhar_id = %s", (aadhar_id, ))
            if cursor.fetchone() is not None:
                error_msg = 'This Aadhar ID is already registered.'
            else:
                cursor.execute("select voter_id from voter_list where voter_card = %s", (voter_card, ))
                if cursor.fetchone() is not None:
                    error_msg = 'This Voter Card Number is already registered.'
                else:
                    cursor.execute("insert into voter_list (name, password_hash, aadhar_id, voter_card, dob, email, contact_no, key_hash, voted, verified) values (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s);", (
                            name,
                            password_hash,
                            aadhar_id,
                            voter_card,
                            dob,
                            email,
                            contact_no,
                            key_hash,
                            verified
                    ))
                    return {'key': key}
    except Exception as e:
        print('Error: ', e)
        error_msg = 'Interval server error in creating Voter ID'
    return {'error': error_msg}


def get_candidate_list() -> list:
    try:
        cursor.execute("select * from candidate_list;")
        rows = cursor.fetchall()
    except:
        check_mysql_connection(cursor)
        try:
            cursor.execute("select * from candidate_list;")
            rows = cursor.fetchall()
        except Exception as e:
            print(str(e))
            return render_template('error.html', error = "Error in fetching data from database.")
    candidateList = []
    for row in rows:
        candidateList.append({
            'candidate_id': row[0],
            'name': row[1],
            'party': row[2]
        })
    return candidateList


def get_results() -> list:
    blockchainResponse = []
    def makeReq(server):
        blockchainResponse.append(requests.get(server + '/get_result').text)
    reqs = []
    for server in BLOCKCHAIN_SERVERS:
        reqs.append(threading.Thread(target=makeReq, args=[server]))
        reqs[-1].start()
    for req in reqs:
        req.join()
    similarResponse = {}
    for res in blockchainResponse:
        if res in similarResponse:
            similarResponse[res] += 1
        else:
            similarResponse[res] = 1
    resultStr = ''
    maxCount = 0
    for res in similarResponse:
        if similarResponse[res] > maxCount:
            maxCount = similarResponse[res]
            resultStr = res
    result = json.loads(resultStr)
    
    candidateList = get_candidate_list()
    for candidate in candidateList:
        if str(candidate['candidate_id']) in result:
            candidate['votes'] = result[str(candidate['candidate_id'])]
        else:
            candidate['votes'] = 0
    return candidateList

    

def check_mysql_connection(cursor):
    try:
        cursor.execute("select * from candidate_list where candidate_id=100001;")
    except Exception as e1:
        print("Reconnecting to database server...")
        print(str(e1))
        try:
            if USING_SQLITE:
                connection = SQLiteConnectionWrapper()
                cursor = connection.cursor()
            else:
                connection = psycopg2.connect(DATABASE_URL, sslmode='require')
                connection.autocommit = True
                cursor = connection.cursor()
            globals()['connection'] = connection
            cursor = connection.cursor()
        except Exception as e:
            print("Error: Unable to connect to database.")
            print("Error: " + str(e))
    globals()['cursor'] = cursor

def login(name, voter_id):
    session.permanent = True
    session['name'] = name
    session['voter_id'] = voter_id

def is_loggedin() -> bool:
    if 'name' in session and 'voter_id' in session:
        return True
    return False
