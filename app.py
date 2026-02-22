import os
import re
import math
import json
import sqlite3
import hashlib
import secrets
import datetime
from collections import Counter
from functools import wraps
from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, send_file, g)
import pdfplumber

# ── Optional: spaCy ─────────────────────────────────────────────────────────
try:
    import spacy
    nlp = spacy.load("en_core_web_sm")
    SPACY_AVAILABLE = True
except Exception:
    SPACY_AVAILABLE = False

# ── Optional: Claude AI ──────────────────────────────────────────────────────
try:
    import anthropic
    CLAUDE_AVAILABLE = bool(os.getenv("ANTHROPIC_API_KEY"))
    _claude_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", "")) if CLAUDE_AVAILABLE else None
except Exception:
    CLAUDE_AVAILABLE = False
    _claude_client = None

# ── Optional: PDF export ─────────────────────────────────────────────────────
try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except Exception:
    FPDF_AVAILABLE = False

# ── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
DATABASE = 'resumeai.db'

# ═══════════════════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════════════════

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT UNIQUE NOT NULL,
                email     TEXT UNIQUE NOT NULL,
                password  TEXT NOT NULL,
                created   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analyses (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                job_title     TEXT,
                company       TEXT,
                ats_score     REAL,
                keyword_score REAL,
                resume_words  INTEGER,
                matched_kw    INTEGER,
                missing_kw    INTEGER,
                suggestions   INTEGER,
                result_json   TEXT,
                created       TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        ''')
        db.commit()

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ═══════════════════════════════════════════════════════════════════════════
#  AUTH HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Login required', 'redirect': '/login'}), 401
        return f(*args, **kwargs)
    return decorated

def login_required_page(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

# ═══════════════════════════════════════════════════════════════════════════
#  NLP CORE
# ═══════════════════════════════════════════════════════════════════════════

STOPWORDS = set([
    'a','an','the','and','or','but','in','on','at','to','for','of','with',
    'by','from','up','about','into','through','during','is','are','was',
    'were','be','been','being','have','has','had','do','does','did','will',
    'would','could','should','may','might','shall','can','i','you','he',
    'she','it','we','they','them','their','this','that','these','those',
    'my','your','his','her','its','our','as','if','then','than','so','yet',
    'not','no','nor','only','own','same','such','too','very','just','also',
    'well','more','most','other','some','any','each','all','few','before',
    'after','above','below','between','us','our','me','him','who','which',
])

ACTION_VERBS = [
    'achieved','built','created','delivered','designed','developed',
    'engineered','established','executed','generated','implemented',
    'improved','increased','launched','led','managed','optimized',
    'reduced','resolved','spearheaded','streamlined','transformed',
    'automated','collaborated','coordinated','deployed','enhanced',
    'facilitated','integrated','maintained','mentored','migrated',
    'produced','scaled','shipped','architected','modernized','analyzed',
    'accelerated','championed','consolidated','directed','drove','earned',
    'expanded','founded','grew','handled','influenced','initiated',
]

SECTION_PATTERNS = {
    'experience':     r'(experience|work history|employment|professional background|career)',
    'education':      r'(education|academic|qualification|degree|university|college)',
    'skills':         r'(skills|technical skills|competencies|expertise|proficiencies)',
    'projects':       r'(projects|portfolio|work samples)',
    'certifications': r'(certifications|certificates|licenses|credentials)',
    'summary':        r'(summary|objective|profile|about me|professional summary)',
    'achievements':   r'(achievements|accomplishments|awards|honors)',
    'contact':        r'(contact|email|phone|linkedin|github)',
}

def extract_pdf_text(filepath):
    text = ""
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                pt = page.extract_text()
                if pt:
                    text += pt + "\n"
    except Exception as e:
        return None, str(e)
    return text.strip(), None

def tokenize(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s\+\#\.]', ' ', text)
    return [t for t in text.split() if len(t) > 1]

def get_keywords(text):
    tokens = tokenize(text)
    filtered = [t for t in tokens if t not in STOPWORDS and len(t) > 2]
    bigrams = [filtered[i]+' '+filtered[i+1] for i in range(len(filtered)-1)]
    return Counter(filtered + bigrams)

def extract_entities_spacy(text):
    """Use spaCy to extract named entities & noun chunks (skills, orgs, roles)."""
    if not SPACY_AVAILABLE:
        return [], []
    doc = nlp(text[:50000])
    entities = [{'text': ent.text, 'label': ent.label_}
                for ent in doc.ents
                if ent.label_ in ('ORG','PRODUCT','SKILL','WORK_OF_ART','GPE','LANGUAGE')]
    noun_chunks = list({chunk.text.lower().strip()
                        for chunk in doc.noun_chunks
                        if len(chunk.text.split()) <= 3 and chunk.text.lower() not in STOPWORDS})[:30]
    return entities, noun_chunks

def cosine_similarity(text1, text2):
    v1, v2 = get_keywords(text1), get_keywords(text2)
    common = set(v1) & set(v2)
    if not common:
        return 0.0
    dot  = sum(v1[k]*v2[k] for k in common)
    mag1 = math.sqrt(sum(x**2 for x in v1.values()))
    mag2 = math.sqrt(sum(x**2 for x in v2.values()))
    return dot/(mag1*mag2) if mag1 and mag2 else 0.0

def get_missing_keywords(resume_text, jd_text, top_n=20):
    jd_kw = get_keywords(jd_text)
    resume_tokens = set(tokenize(resume_text))
    missing = []
    for kw, cnt in jd_kw.most_common(100):
        if kw in STOPWORDS or len(kw) < 3:
            continue
        kw_parts = kw.split()
        found = any(any(p in t or t in p for t in resume_tokens) for p in kw_parts)
        if not found:
            missing.append({'keyword': kw, 'frequency': cnt})
        if len(missing) >= top_n:
            break
    return missing

def detect_sections(text):
    tl = text.lower()
    return {sec: bool(re.search(pat, tl)) for sec, pat in SECTION_PATTERNS.items()}

def check_quantifiable(text):
    return len(re.findall(r'\b\d+[\+%xX]?\b', text))

def check_action_verbs(text):
    tl = text.lower()
    used    = [v for v in ACTION_VERBS if v in tl]
    missing = [v for v in ACTION_VERBS[:15] if v not in tl]
    return used, missing

def check_contact(text):
    return {
        'email':    bool(re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)),
        'phone':    bool(re.search(r'(\+?\d[\d\s\-\(\)]{8,15}\d)', text)),
        'linkedin': bool(re.search(r'linkedin\.com', text, re.I)),
        'github':   bool(re.search(r'github\.com',   text, re.I)),
    }

def estimate_length(text):
    w = len(text.split())
    status = 'too_short' if w < 200 else ('too_long' if w > 1000 else 'good')
    return status, w

# ═══════════════════════════════════════════════════════════════════════════
#  CLAUDE AI SUGGESTIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_claude_suggestions(resume_text, jd_text, score, missing_kw):
    """Call Claude API to get smart, context-aware suggestions."""
    if not CLAUDE_AVAILABLE or not _claude_client:
        return None
    try:
        missing_list = ', '.join([k['keyword'] for k in missing_kw[:8]])
        prompt = f"""You are an expert ATS resume consultant. Analyze this resume against the job description.

ATS Score: {score}/100
Missing Keywords: {missing_list}

RESUME (excerpt):
{resume_text[:2000]}

JOB DESCRIPTION (excerpt):
{jd_text[:1500]}

Give exactly 4 specific, actionable improvement suggestions. For each suggestion:
- Be specific (mention exact skills, sections, or phrases to add)
- Explain WHY it matters for this specific role
- Keep each suggestion under 60 words

Format as JSON array:
[
  {{"icon": "emoji", "category": "Critical|Important|Nice to Have", "title": "Short title", "detail": "Specific advice..."}}
]

Return ONLY the JSON array, nothing else."""

        msg = _claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        # Clean up
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'^```\s*',     '', raw)
        raw = re.sub(r'\s*```$',     '', raw)
        return json.loads(raw)
    except Exception as e:
        print(f"Claude API error: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
#  RULE-BASED SUGGESTIONS (fallback)
# ═══════════════════════════════════════════════════════════════════════════

def generate_rule_suggestions(resume_text, jd_text, sections, contact, missing_kw, metrics, used_verbs):
    sugg = []
    if not contact['email']:
        sugg.append({'icon':'📧','category':'Critical','title':'Add Email Address',
            'detail':'Your resume is missing an email address — recruiters cannot contact you without it.'})
    if not contact['phone']:
        sugg.append({'icon':'📱','category':'Critical','title':'Add Phone Number',
            'detail':'Include a phone number so hiring managers can schedule interviews quickly.'})
    if not contact['linkedin']:
        sugg.append({'icon':'💼','category':'Important','title':'Add LinkedIn Profile',
            'detail':'87% of recruiters check LinkedIn. Adding your URL increases callback chances significantly.'})
    if not contact['github'] and any(w in jd_text.lower() for w in ['developer','engineer','software','code']):
        sugg.append({'icon':'🐙','category':'Important','title':'Add GitHub Profile',
            'detail':'Tech recruiters actively check GitHub. Showcase your projects to stand out.'})
    if not sections.get('summary'):
        sugg.append({'icon':'📝','category':'Important','title':'Add Professional Summary',
            'detail':'A 3-4 line tailored summary at the top dramatically improves ATS and recruiter first impression.'})
    if not sections.get('skills'):
        sugg.append({'icon':'⚡','category':'Critical','title':'Add Skills Section',
            'detail':'ATS systems scan skills sections first. List all technical and soft skills explicitly.'})
    if not sections.get('achievements'):
        sugg.append({'icon':'🏆','category':'Nice to Have','title':'Add Achievements Section',
            'detail':'Highlighting 3-5 top career wins sets you apart from candidates with similar experience.'})
    if metrics < 3:
        sugg.append({'icon':'📊','category':'Important','title':'Add Quantifiable Metrics',
            'detail':f'Only {metrics} measurable result(s) found. Add numbers: "Increased sales 35%" or "Managed 8-person team".'})
    if len(used_verbs) < 5:
        sugg.append({'icon':'💡','category':'Important','title':'Strengthen Action Verbs',
            'detail':f'Only {len(used_verbs)} action verbs detected. Start bullets with: {", ".join(ACTION_VERBS[:6])}.'})
    if len(missing_kw) > 5:
        top = ', '.join(k['keyword'] for k in missing_kw[:6])
        sugg.append({'icon':'🔑','category':'Critical','title':'Add Missing Job Keywords',
            'detail':f'Naturally weave these into your resume: {top}. ATS filters depend on exact keyword matching.'})
    status, wc = estimate_length(resume_text)
    if status == 'too_short':
        sugg.append({'icon':'📄','category':'Important','title':'Expand Resume Content',
            'detail':f'Only ~{wc} words found. Aim for 400–700 words with detailed bullet points and project descriptions.'})
    elif status == 'too_long':
        sugg.append({'icon':'✂️','category':'Nice to Have','title':'Trim Resume Length',
            'detail':f'~{wc} words is long. Trim to 1–2 pages by removing dated or irrelevant experience.'})
    return sugg

# ═══════════════════════════════════════════════════════════════════════════
#  SCORING ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def calculate_ats_score(resume_text, jd_text):
    sections    = detect_sections(resume_text)
    contact     = check_contact(resume_text)
    missing_kw  = get_missing_keywords(resume_text, jd_text)
    metrics     = check_quantifiable(resume_text)
    used_verbs, _ = check_action_verbs(resume_text)
    length_status, word_count = estimate_length(resume_text)

    # spaCy extras
    entities, noun_chunks = extract_entities_spacy(resume_text)

    sim            = cosine_similarity(resume_text, jd_text)
    keyword_score  = min(sim * 150, 40)
    req_sections   = ['experience','education','skills','summary']
    present        = sum(1 for s in req_sections if sections.get(s))
    section_score  = (present / len(req_sections)) * 20
    contact_score  = min(contact['email']*4 + contact['phone']*3 + contact['linkedin']*2 + contact['github']*1, 10)
    verb_score     = min(len(used_verbs)/8*10, 10)
    quant_score    = min(metrics/5*10, 10)
    length_score   = 10 if length_status=='good' else (5 if length_status=='too_long' else 3)

    total = min(round(keyword_score+section_score+contact_score+verb_score+quant_score+length_score, 1), 100)

    # Matched keywords
    jd_kw = get_keywords(jd_text)
    resume_tokens = set(tokenize(resume_text))
    matched = []
    for kw, freq in jd_kw.most_common(60):
        if kw in STOPWORDS or len(kw) < 3:
            continue
        if all(any(p in t or t == p for t in resume_tokens) for p in kw.split()):
            matched.append({'keyword': kw, 'frequency': freq})
        if len(matched) >= 20:
            break

    # Claude suggestions (if API key set), else rule-based
    ai_suggestions = get_claude_suggestions(resume_text, jd_text, total, missing_kw)
    if ai_suggestions:
        suggestions = ai_suggestions
        suggestion_source = 'claude'
    else:
        suggestions = generate_rule_suggestions(resume_text, jd_text, sections, contact, missing_kw, metrics, used_verbs)
        suggestion_source = 'rules'

    return {
        'total_score':      total,
        'suggestion_source': suggestion_source,
        'breakdown': {
            'keyword_match': round(keyword_score,1), 'keyword_max': 40,
            'sections':      round(section_score,1), 'sections_max': 20,
            'contact':       round(contact_score,1), 'contact_max':  10,
            'action_verbs':  round(verb_score,1),    'action_verbs_max': 10,
            'quantification':round(quant_score,1),   'quantification_max': 10,
            'length':        round(length_score,1),  'length_max': 10,
        },
        'sections_found':   sections,
        'contact_info':     contact,
        'matched_keywords': matched,
        'missing_keywords': missing_kw[:20],
        'suggestions':      suggestions,
        'spacy_entities':   entities[:15],
        'spacy_chunks':     noun_chunks[:20],
        'stats': {
            'word_count':        word_count,
            'metric_count':      metrics,
            'action_verbs_used': len(used_verbs),
            'sections_present':  present,
            'spacy_available':   SPACY_AVAILABLE,
            'claude_available':  CLAUDE_AVAILABLE,
        }
    }

# ═══════════════════════════════════════════════════════════════════════════
#  PDF EXPORT
# ═══════════════════════════════════════════════════════════════════════════

def generate_pdf_report(data, username):
    """Generate a PDF report from analysis results."""
    if not FPDF_AVAILABLE:
        return None, "fpdf2 not installed. Run: pip install fpdf2"
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)

        # Header
        pdf.set_fill_color(15, 15, 25)
        pdf.rect(0, 0, 210, 40, 'F')
        pdf.set_font('Helvetica', 'B', 22)
        pdf.set_text_color(184, 255, 87)
        pdf.cell(0, 15, '', ln=True)
        pdf.cell(0, 15, 'ResumeAI — ATS Analysis Report', align='C', ln=True)
        pdf.set_text_color(150, 150, 170)
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(0, 8, f'Generated for: {username}  |  {datetime.datetime.now().strftime("%B %d, %Y %H:%M")}', align='C', ln=True)
        pdf.ln(12)

        # Score
        score = data['total_score']
        pdf.set_text_color(20, 20, 30)
        pdf.set_fill_color(240, 250, 230)
        pdf.set_font('Helvetica', 'B', 36)
        pdf.set_text_color(40, 120, 40)
        pdf.cell(0, 18, f'ATS Score: {score} / 100', align='C', ln=True)
        pdf.set_font('Helvetica', '', 12)
        if score >= 80:   verdict = 'Excellent Match — Ready to Apply!'
        elif score >= 65: verdict = 'Good Match — Minor Improvements Needed'
        elif score >= 45: verdict = 'Fair Match — Significant Improvements Needed'
        else:             verdict = 'Poor Match — Major Revisions Required'
        pdf.set_text_color(80, 80, 90)
        pdf.cell(0, 8, verdict, align='C', ln=True)
        pdf.ln(8)

        # Divider
        pdf.set_draw_color(200, 200, 210)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(6)

        # Breakdown
        pdf.set_font('Helvetica', 'B', 13)
        pdf.set_text_color(20, 20, 30)
        pdf.cell(0, 8, 'Score Breakdown', ln=True)
        pdf.ln(2)
        bd = data['breakdown']
        rows = [
            ('Keyword Match',   bd['keyword_match'],   bd['keyword_max']),
            ('Resume Sections', bd['sections'],        bd['sections_max']),
            ('Contact Info',    bd['contact'],         bd['contact_max']),
            ('Action Verbs',    bd['action_verbs'],    bd['action_verbs_max']),
            ('Quantification',  bd['quantification'],  bd['quantification_max']),
            ('Length & Format', bd['length'],          bd['length_max']),
        ]
        pdf.set_font('Helvetica', '', 11)
        for label, got, mx in rows:
            pct = int((got/mx)*100)
            pdf.set_text_color(60,60,70)
            pdf.cell(80, 7, label)
            pdf.cell(30, 7, f'{got} / {mx}')
            # Bar
            bar_w = 70
            filled = int(bar_w * got / mx)
            pdf.set_fill_color(230,230,235)
            pdf.rect(pdf.get_x(), pdf.get_y()+1, bar_w, 4, 'F')
            r,g,b = (74,222,128) if pct>=70 else (250,204,21) if pct>=40 else (248,113,113)
            pdf.set_fill_color(r,g,b)
            if filled > 0:
                pdf.rect(pdf.get_x(), pdf.get_y()+1, filled, 4, 'F')
            pdf.set_text_color(100,100,110)
            pdf.cell(bar_w+5, 7, '')
            pdf.cell(0, 7, f'{pct}%', ln=True)
        pdf.ln(6)

        # Matched keywords
        pdf.line(15, pdf.get_y(), 195, pdf.get_y()); pdf.ln(4)
        pdf.set_font('Helvetica','B',13); pdf.set_text_color(20,20,30)
        pdf.cell(0,8,'Matched Keywords',ln=True); pdf.ln(2)
        pdf.set_font('Helvetica','',10); pdf.set_text_color(40,140,70)
        matched_str = '  ✓  '.join(k['keyword'] for k in data['matched_keywords'][:15])
        pdf.multi_cell(0,6, matched_str or 'None found')
        pdf.ln(4)

        # Missing keywords
        pdf.line(15, pdf.get_y(), 195, pdf.get_y()); pdf.ln(4)
        pdf.set_font('Helvetica','B',13); pdf.set_text_color(20,20,30)
        pdf.cell(0,8,'Missing Keywords (Add These)',ln=True); pdf.ln(2)
        pdf.set_font('Helvetica','',10); pdf.set_text_color(180,40,40)
        missing_str = '  ✗  '.join(k['keyword'] for k in data['missing_keywords'][:15])
        pdf.multi_cell(0,6, missing_str or 'None — great job!')
        pdf.ln(4)

        # Suggestions
        pdf.line(15, pdf.get_y(), 195, pdf.get_y()); pdf.ln(4)
        pdf.set_font('Helvetica','B',13); pdf.set_text_color(20,20,30)
        pdf.cell(0,8,'Improvement Suggestions',ln=True); pdf.ln(2)
        pdf.set_font('Helvetica','',11)
        for i, s in enumerate(data['suggestions'], 1):
            cat_color = (180,40,40) if s['category']=='Critical' else \
                        (160,110,0) if s['category']=='Important' else (60,100,160)
            pdf.set_text_color(*cat_color)
            pdf.set_font('Helvetica','B',11)
            pdf.cell(0,7,f"{i}. [{s['category']}] {s['title']}",ln=True)
            pdf.set_font('Helvetica','',10)
            pdf.set_text_color(70,70,80)
            pdf.multi_cell(0,6,'   '+s['detail'])
            pdf.ln(2)

        # Footer
        pdf.set_y(-20)
        pdf.set_font('Helvetica','',8)
        pdf.set_text_color(150,150,160)
        pdf.cell(0,6,'ResumeAI — AI Resume Analyzer + ATS Checker  |  No data stored after analysis', align='C')

        path = os.path.join('uploads', f'report_{secrets.token_hex(8)}.pdf')
        pdf.output(path)
        return path, None
    except Exception as e:
        return None, str(e)

# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES — AUTH
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    if 'user_id' in session:
        return render_template('index.html',
            username=session['username'],
            spacy_available=SPACY_AVAILABLE,
            claude_available=CLAUDE_AVAILABLE,
            fpdf_available=FPDF_AVAILABLE)
    return redirect('/login')

@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect('/')
    return render_template('login.html')

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    username = (data.get('username','') or '').strip()
    email    = (data.get('email','')    or '').strip().lower()
    password = (data.get('password','') or '').strip()

    if not username or not email or not password:
        return jsonify({'error': 'All fields required'}), 400
    if len(username) < 3:
        return jsonify({'error': 'Username must be at least 3 characters'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    if not re.match(r'[^@]+@[^@]+\.[^@]+', email):
        return jsonify({'error': 'Invalid email address'}), 400

    db = get_db()
    try:
        db.execute(
            'INSERT INTO users (username, email, password, created) VALUES (?,?,?,?)',
            (username, email, hash_password(password), datetime.datetime.utcnow().isoformat())
        )
        db.commit()
        user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        session['user_id']  = user['id']
        session['username'] = user['username']
        return jsonify({'success': True, 'username': username})
    except sqlite3.IntegrityError as e:
        if 'username' in str(e):
            return jsonify({'error': 'Username already taken'}), 400
        return jsonify({'error': 'Email already registered'}), 400

@app.route('/api/login', methods=['POST'])
def login():
    data     = request.get_json()
    email    = (data.get('email','')    or '').strip().lower()
    password = (data.get('password','') or '').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    db = get_db()
    user = db.execute('SELECT * FROM users WHERE email=? AND password=?',
                      (email, hash_password(password))).fetchone()
    if not user:
        return jsonify({'error': 'Invalid email or password'}), 401

    session['user_id']  = user['id']
    session['username'] = user['username']
    return jsonify({'success': True, 'username': user['username']})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/me')
def me():
    if 'user_id' not in session:
        return jsonify({'logged_in': False})
    return jsonify({'logged_in': True, 'username': session['username'], 'user_id': session['user_id']})

# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES — ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    if 'resume' not in request.files:
        return jsonify({'error': 'No resume file uploaded'}), 400

    file     = request.files['resume']
    jd_text  = (request.form.get('job_description','') or '').strip()
    job_title= (request.form.get('job_title','')       or '').strip()
    company  = (request.form.get('company','')         or '').strip()

    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are supported'}), 400
    if len(jd_text) < 50:
        return jsonify({'error': 'Please provide a job description (at least 50 characters)'}), 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], f'resume_{secrets.token_hex(6)}.pdf')
    file.save(filepath)

    resume_text, err = extract_pdf_text(filepath)
    try: os.remove(filepath)
    except: pass

    if err:
        return jsonify({'error': f'PDF parse error: {err}'}), 500
    if not resume_text or len(resume_text) < 50:
        return jsonify({'error': 'Cannot extract text from this PDF. Ensure it is not a scanned image.'}), 400

    results = calculate_ats_score(resume_text, jd_text)
    results['resume_word_count'] = len(resume_text.split())
    results['resume_preview']    = resume_text[:400] + '...' if len(resume_text) > 400 else resume_text

    # Save to history
    try:
        db = get_db()
        cur = db.execute(
            '''INSERT INTO analyses
               (user_id,job_title,company,ats_score,keyword_score,resume_words,
                matched_kw,missing_kw,suggestions,result_json,created)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (session['user_id'], job_title, company,
             results['total_score'], results['breakdown']['keyword_match'],
             results['resume_word_count'],
             len(results['matched_keywords']), len(results['missing_keywords']),
             len(results['suggestions']),
             json.dumps(results),
             datetime.datetime.utcnow().isoformat())
        )
        db.commit()
        results['analysis_id'] = cur.lastrowid
    except Exception as e:
        print(f"DB save error: {e}")
        results['analysis_id'] = None

    return jsonify(results)

# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES — HISTORY
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/api/history')
@login_required
def history():
    db   = get_db()
    rows = db.execute(
        '''SELECT id,job_title,company,ats_score,matched_kw,missing_kw,
                  suggestions,created
           FROM analyses WHERE user_id=? ORDER BY created DESC LIMIT 20''',
        (session['user_id'],)
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/history/<int:analysis_id>')
@login_required
def get_analysis(analysis_id):
    db  = get_db()
    row = db.execute(
        'SELECT * FROM analyses WHERE id=? AND user_id=?',
        (analysis_id, session['user_id'])
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    data = json.loads(row['result_json'])
    data['job_title'] = row['job_title']
    data['company']   = row['company']
    data['created']   = row['created']
    return jsonify(data)

@app.route('/api/history/<int:analysis_id>', methods=['DELETE'])
@login_required
def delete_analysis(analysis_id):
    db = get_db()
    db.execute('DELETE FROM analyses WHERE id=? AND user_id=?',
               (analysis_id, session['user_id']))
    db.commit()
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES — PDF EXPORT
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/export/<int:analysis_id>')
@login_required
def export_pdf(analysis_id):
    db  = get_db()
    row = db.execute('SELECT * FROM analyses WHERE id=? AND user_id=?',
                     (analysis_id, session['user_id'])).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    data = json.loads(row['result_json'])
    path, err = generate_pdf_report(data, session['username'])
    if err:
        return jsonify({'error': err}), 500

    def cleanup(resp):
        try: os.remove(path)
        except: pass
        return resp

    resp = send_file(path, as_attachment=True,
                     download_name=f'ATS_Report_{analysis_id}.pdf',
                     mimetype='application/pdf')
    return cleanup(resp)

@app.route('/export/current', methods=['POST'])
@login_required
def export_current():
    """Export the most-recent analysis for this user."""
    db  = get_db()
    row = db.execute(
        'SELECT * FROM analyses WHERE user_id=? ORDER BY created DESC LIMIT 1',
        (session['user_id'],)
    ).fetchone()
    if not row:
        return jsonify({'error': 'No analysis found. Run an analysis first.'}), 404

    data = json.loads(row['result_json'])
    path, err = generate_pdf_report(data, session['username'])
    if err:
        return jsonify({'error': f'PDF generation failed: {err}. Install fpdf2 with: pip install fpdf2'}), 500

    resp = send_file(path, as_attachment=True,
                     download_name='ATS_Report.pdf',
                     mimetype='application/pdf')
    try: os.remove(path)
    except: pass
    return resp

# ═══════════════════════════════════════════════════════════════════════════
#  BOOT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    os.makedirs('uploads', exist_ok=True)
    init_db()
    print(f"\n{'='*50}")
    print(f"  ResumeAI v2 — Advanced ATS Analyzer")
    print(f"  spaCy NLP  : {'✅ Active' if SPACY_AVAILABLE else '⚠️  Not installed (pip install spacy && python -m spacy download en_core_web_sm)'}")
    print(f"  Claude AI  : {'✅ Active' if CLAUDE_AVAILABLE else '⚠️  Set ANTHROPIC_API_KEY env var'}")
    print(f"  PDF Export : {'✅ Active' if FPDF_AVAILABLE else '⚠️  Not installed (pip install fpdf2)'}")
    print(f"  Running at : http://localhost:5000")
    print(f"{'='*50}\n")
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=False, host="0.0.0.0", port=port)
else:
    # This runs when gunicorn starts
    os.makedirs('uploads', exist_ok=True)
    init_db()