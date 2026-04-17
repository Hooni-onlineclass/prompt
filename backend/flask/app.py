
import os
import sqlite3
from queue import Queue, Empty
from flask import Flask, send_from_directory, request, jsonify, Response, g

# 1. 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "../../frontend"))

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')

# 전역 클라이언트 리스트 (실시간 로그 브로드캐스트용)
clients = []

# [라운드 주제 설정: 1단계부터 10단계까지 빌드업] - 기존 로직 유지
ROUND_TOPICS = {
    1: "라면 맛있게 설명하기 (기초)",
    2: "안성시 장점 홍보 (기초)",
    3: "[역할] 요리사가 되어 라면 설명하기",
    4: "[대상] 초등학생에게 안성시 설명하기",
    5: "[형식] 학교 자랑거리 3가지 요약하기",
    6: "[제약] '물' 단어 없이 라면 설명하기",
    7: "[매체] SNS 스타일로 안성시 홍보하기",
    8: "[문체] 인터뷰 형식으로 학교 자랑하기",
    9: "[종합] 요리사가 자취생에게 표로 설명하기",
    10: "[자유] AI 엔지니어로서 미래 상상하기"
}

# [선생님 모범 답안] - 기존 로직 유지
MODEL_ANSWERS = {
    1: "라면 맛있게 끓이는 법 알려줘.",
    2: "안성시의 자연경관과 교통의 장점을 알려줘.",
    3: "너는 30년 경력의 요리사야. 자취생도 따라 할 수 있는 황금 라면 레시피를 알려줘.",
    4: "초등학생도 이해할 수 있게 쉬운 단어를 사용해서 안성의 보석 같은 장소들을 설명해줘.",
    5: "우리 학교의 급식, 운동장, 선생님들에 대해 번호를 매겨서 3줄로 요약해줘.",
    6: "H2O를 이용한 조리법을 알려줘. (단, '물'이라는 글자는 절대 포함하지 마.)",
    7: "안성 남사당패 공연을 포함해서 인스타그램에 올릴 홍보글을 해시태그와 함께 작성해줘.",
    8: "우리 학교 학생회장과 인터뷰하는 형식으로 축제 분위기를 생생하게 묘사해줘.",
    9: "요리 전문가로서 라면 조리법을 '준비물, 조리단계, 꿀팁'으로 나누어 표 형식으로 정리해줘.",
    10: "미래 학교에서 AI가 학생 개개인의 진도를 관리해주는 시나리오를 엔지니어 시각에서 전문적으로 설계해봐."
}

# --- [핵심: 프롬프트 채점 엔진] - 기존 로직 유지 ---
def analyze_prompt(text):
    if not text or len(text.strip()) == 0:
        return 40, "내용을 입력해주세요!"
    score = 40
    feedback = []
    
    # 1. 길이 체크
    if len(text) > 30:
        score += 20
        feedback.append("상세한 지시(+20)")
    elif len(text) > 15:
        score += 10
        feedback.append("적절한 길이(+10)")
        
    # 2. 페르소나 체크
    personas = ['전문가', '선생님', '요리사', '박사', '기자', '가이드', '친구', '작가', '엔지니어']
    if any(p in text for p in personas):
        score += 15
        feedback.append("역할 지정(+15)")
        
    # 3. 구조화 체크
    structures = ['단계', '순서', '방법', '번호', '리스트', '요약', '표로', '구조']
    if any(s in text for s in structures):
        score += 15
        feedback.append("구조화 요청(+15)")
        
    # 4. 말투 체크
    tones = ['친절하게', '유머러스', '진지하게', '공식적', '쉽게', '세세하게', '전문적']
    if any(t in text for t in tones):
        score += 10
        feedback.append("말투 지정(+10)")
        
    return min(score, 100), " / ".join(feedback) if feedback else "더 구체적인 조건을 추가해보세요!"

# --- [DB 관리 섹션: 100명 동시 접속 보완] ---
def get_db():
    if 'db' not in g:
        db_path = os.path.join(BASE_DIR, "challenge.db")
        # [보완] timeout=30 추가: 100명이 동시에 몰려도 에러 없이 30초간 대기하며 순차 처리
        g.db = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e):
    db = g.pop('db', None)
    if db is not None: db.close()

def init_db():
    db_path = os.path.join(BASE_DIR, "challenge.db")
    with sqlite3.connect(db_path) as conn:
        # [보완] WAL 모드 활성화: 읽기와 쓰기를 분리해 100명 동시 접속 시 병목 현상 제거
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            round INTEGER,
            prompt TEXT,
            result_local TEXT,
            result_external TEXT,
            score INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        
        # 기존 필드 체크 로직 유지
        cursor = conn.execute("PRAGMA table_info(prompts)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'score' not in columns:
            conn.execute("ALTER TABLE prompts ADD COLUMN score INTEGER DEFAULT 0")
        if 'timestamp' not in columns:
            try: conn.execute("ALTER TABLE prompts ADD COLUMN timestamp DATETIME")
            except: pass

init_db()

# --- [라우트 섹션] ---

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "prompt.html")

@app.route("/join", methods=["POST"])
def join():
    data = request.json
    name = data.get("name", "익명").strip()
    msg = f"🚀 <b>{name}</b> 엔지니어님이 시스템에 접속했습니다."
    for q in clients[:]:
        try: q.put(msg)
        except: pass
    return jsonify({"msg": "Welcome", "name": name})

@app.route("/api/prompt", methods=["POST"])
def prompt():
    data = request.json
    name = data.get("name", "익명")
    prompt_text = data.get("prompt", "")
    try: round_num = int(data.get("round", 1))
    except: round_num = 1

    # 기존 채점 진행
    score, analysis = analyze_prompt(prompt_text)
    
    # 6라운드: '물' 단어 사용 금지 제약 조건 유지
    if round_num == 6 and "물" in prompt_text:
        score = 0
        analysis = "⚠️ 제약 조건 위반: '물'이라는 단어를 사용할 수 없습니다!"
    
    # 9~10라운드: 구조화 미션 로직 유지
    if round_num >= 9:
        if not any(keyword in prompt_text for keyword in ["표", "단계", "구조", "리스트"]):
            score = min(score, 65)
            analysis += " (마스터 미션: 데이터의 '구조화(표 등)' 요청이 부족합니다.)"

    topic = ROUND_TOPICS.get(round_num, "자유 주제")
    teacher_tip = MODEL_ANSWERS.get(round_num, "멋진 프롬프트를 기대합니다!")
    
    local_result = f"★ 점수: {score}점\n분석: {analysis}\n\n💡 선생님의 Tip: \"{teacher_tip}\""
    
    if score >= 80:
        external_result = f"AI: 와! 완벽한 명령어입니다. '{topic}'에 대해 수준 높은 답변을 준비하겠습니다."
    elif score >= 60:
        external_result = f"AI: 좋은 프롬프트입니다. 요청하신 내용을 분석 중입니다."
    else:
        external_result = f"AI: 질문이 구체적이지 않아 일반적인 답변을 드립니다."

    # DB 저장
    db = get_db()
    db.execute("""INSERT INTO prompts (name, round, prompt, result_local, result_external, score) 
                  VALUES (?, ?, ?, ?, ?, ?)""",
               (name, round_num, prompt_text, local_result, external_result, score))
    db.commit()

    # 실시간 로그 브로드캐스트
    broadcast_msg = f"<span style='color:#ffb703'>[Rd {round_num}]</span> <span class='log-name'>{name}</span>({score}점): {prompt_text}"
    for q in clients[:]:
        try: q.put(broadcast_msg)
        except: pass
        
    return jsonify({"local": local_result, "external": external_result})

@app.route("/api/ranking")
def get_ranking():
    try:
        db = get_db()
        # 라운드별 최고 점수 합산 랭킹 로직 유지
        query = """
            SELECT name, SUM(max_score) as total_score, COUNT(round) as completed_rounds
            FROM (
                SELECT name, round, MAX(score) as max_score
                FROM prompts
                GROUP BY name, round
            )
            GROUP BY name
            ORDER BY total_score DESC, completed_rounds DESC
            LIMIT 10
        """
        cur = db.execute(query)
        ranking = [dict(row) for row in cur.fetchall()]
        return jsonify(ranking)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/dashboard")
def admin_page():
    return send_from_directory(FRONTEND_DIR, "admin.html")

@app.route("/api/all_results")
def get_all_results():
    try:
        db = get_db()
        cur = db.execute("SELECT * FROM prompts ORDER BY id DESC")
        rows = cur.fetchall()
        results = []
        for row in rows:
            r = dict(row)
            results.append({
                "id": r.get("id"),
                "name": r.get("name", "익명"),
                "round": r.get("round", 0),
                "prompt": r.get("prompt", ""),
                "score": r.get("score", 0),
                "time": str(r.get("timestamp")) if r.get("timestamp") else "-"
            })
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/stream")
def stream():
    def event_stream():
        q = Queue()
        clients.append(q)
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f"data: {msg}\n\n"
                except Empty:
                    yield "data: ping\n\n"
        finally:
            if q in clients: clients.remove(q)
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/stats")
def stats():
    return jsonify({"count": len(clients)})

if __name__ == "__main__":
    # 100명 동시 접속을 위해 threaded=True 옵션 유지 (Gunicorn 미사용 시 대비)
    app.run(host="0.0.0.0", port=7001, threaded=True)
