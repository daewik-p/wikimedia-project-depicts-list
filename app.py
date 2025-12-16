import os
import requests
import io
import uuid
from flask import Flask, render_template, request, jsonify, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    print("Warning: Pillow not installed. Image uploads will be disabled.")

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_default")

# Configuration
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'images.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'images')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)

# --- Models ---
class ImageModel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(100), nullable=False) # The optimized original
    thumbnail_filename = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(200), nullable=True)
    
    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "original_url": f"/static/images/{self.filename}",
            "thumbnail_url": f"/static/images/{self.thumbnail_filename}"
        }

# --- Helpers ---
def process_image(file_storage):
    """
    Optimizes the uploaded image:
    1. Converts to WebP (q=80)
    2. Generates a thumbnail (max 300px, LANCZOS)
    Returns tuple (original_filename, thumbnail_filename)
    """
    if not PILLOW_AVAILABLE:
        raise ImportError("Pillow library is not available. Image processing is disabled.")

    # Generate unique filenames
    unique_id = uuid.uuid4().hex
    original_filename = f"{unique_id}.webp"
    thumbnail_filename = f"{unique_id}_thumb.webp"
    
    original_path = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
    thumbnail_path = os.path.join(app.config['UPLOAD_FOLDER'], thumbnail_filename)

    # Open image using Pillow
    img = Image.open(file_storage)
    
    # Allow simple format conversion (RGB for WebP)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")

    # 1. Save Optimized Original
    img.save(original_path, "WEBP", quality=80, optimize=True)

    # 2. Generate Thumbnail
    img.thumbnail((300, 300), Image.Resampling.LANCZOS)
    img.save(thumbnail_path, "WEBP", quality=80, optimize=True)

    return original_filename, thumbnail_filename

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
BOT_USERNAME = os.getenv("BOT_USERNAME")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")

USER_AGENT = "DepictsEditor/1.0 (https://github.com/example/depicts-editor; tool_maintainer@example.com)"

def get_commons_session():
    """Authenticates and returns a session with a CSRF token."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})

    # 1. Login
    if BOT_USERNAME and BOT_PASSWORD:
        login_token = s.get(COMMONS_API, params={
            "action": "query",
            "meta": "tokens",
            "type": "login",
            "format": "json"
        }).json()["query"]["tokens"]["logintoken"]
        
        login_req = s.post(COMMONS_API, data={
            "action": "login",
            "lgname": BOT_USERNAME,
            "lgpassword": BOT_PASSWORD,
            "lgtoken": login_token,
            "format": "json"
        })
        
        # Check if login was successful
        # (For this prototype, we'll proceed, but in prod we should check result)

    return s

def get_csrf_token(s):
    """Gets a CSRF token for edit actions."""
    tokens = s.get(COMMONS_API, params={
        "action": "query",
        "meta": "tokens",
        "type": "csrf",
        "format": "json"
    }).json()
    return tokens["query"]["tokens"]["csrftoken"]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/search")
def search():
    try:
        return _search_impl()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

def _search_impl():
    """
    Search Commons for images in a Category (and immediate subcategories).
    """
    query = request.args.get("q")
    page = request.args.get("page", 1, type=int)
    
    if not query:
        return jsonify({"error": "No query provided"}), 400

    # Ensure "Category:" prefix
    cat_title = query.strip()
    if not cat_title.lower().startswith("category:"):
        cat_title = f"Category:{cat_title}"

    files = []
    
    PER_PAGE = 10
    target_count = page * PER_PAGE
    # We fetch one extra item to check if there is a next page
    needed_count = target_count + 1
    
    # Helper to fetch members
    def fetch_members(title, limit=50):
        params = {
            "action": "query",
            "generator": "categorymembers",
            "gcmtitle": title,
            "gcmtype": "file|subcat",
            "gcmlimit": limit, # Dynamic limit
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 320,
            "format": "json"
        }
        return requests.get(COMMONS_API, params=params, headers={"User-Agent": USER_AGENT}).json()

    # 1. Fetch from Main Category
    # Allow fetching slightly more than strictly needed to account for non-files and potential recursion needs (though naive)
    # A true robust offset-based pagination would need a continuation token from MediaWiki API, but for this hybrid approach we'll fetch up to 'needed_count' + buffer
    # Since we can't reliably jump to an offset without a token, we re-fetch from start. This is inefficient for deep pages but consistent.
    
    fetch_limit = min(needed_count + 50, 500) # Cap at 500
    
    data = fetch_members(cat_title, limit=fetch_limit)
    subcats = []
    seen_pageids = set()

    def add_file(page_id, page_data):
        page_id_str = str(page_id)
        if page_id_str in seen_pageids:
            return
        
        info = page_data.get("imageinfo", [{}])[0]
        files.append({
            "pageid": page_id_str,
            "title": page_data["title"],
            "url": info.get("url"),
            "thumb_url": info.get("thumburl", info.get("url")),
            "description": info.get("extmetadata", {}).get("ImageDescription", {}).get("value", "No description"),
            "depicts": [] # Initialize
        })
        seen_pageids.add(page_id_str)

    if "query" in data and "pages" in data["query"]:
        for page_id, p_data in data["query"]["pages"].items():
            if p_data["ns"] == 6: # File
                if "imageinfo" in p_data:
                     add_file(page_id, p_data)
            elif p_data["ns"] == 14: # Category
                subcats.append(p_data["title"])

    # 2. Simple Recursion (Depth 1) - If needed
    # Only recurse if we haven't met the target for the CURRENT page AND future checks
    
    # Recursion Strategy: If we have enough files for this page + next indicator, stop.
    if len(files) < needed_count:
        for subcat in subcats[:5]: # Search first 5 subcats
            if len(files) >= needed_count:
                break
            
            # Fetch remaining needed
            remaining = needed_count - len(files)
            # Fetch a batch from subcat
            sub_limit = min(remaining + 20, 500)
            
            sub_data = fetch_members(subcat, limit=sub_limit)
            if "query" in sub_data and "pages" in sub_data["query"]:
                 for page_id, p_data in sub_data["query"]["pages"].items():
                    if p_data["ns"] == 6 and "imageinfo" in p_data:
                         add_file(page_id, p_data)
    
    # Determine result slice
    start_idx = (page - 1) * PER_PAGE
    end_idx = start_idx + PER_PAGE
    
    has_next = len(files) > target_count
    
    # Slice the files for the response
    current_page_files = files[start_idx:end_idx]

    # 3. Batch Fetch Depicts (P180) - ONLY for the sliced files
    if current_page_files:
        # Get M-IDs
        mids = [f"M{f['pageid']}" for f in current_page_files]
        
        # Batch fetch SDC from Commons
        s_params = {
            "action": "wbgetentities",
            "ids": "|".join(mids),
            "format": "json"
        }
        s_resp = requests.get(COMMONS_API, params=s_params, headers={"User-Agent": USER_AGENT}).json()
        
        all_qids = set()
        file_to_qids = {} # mid -> [qid, ...]

        if "entities" in s_resp:
            for mid, entity in s_resp["entities"].items():
                p180 = []
                claims = entity.get("statements", {}).get("P180", [])
                for claim in claims:
                    if claim.get("mainsnak", {}).get("snaktype") == "value":
                        val = claim["mainsnak"]["datavalue"]["value"]
                        if val.get("entity-type") == "item": # Should be item
                             qid = val["id"]
                             p180.append(qid)
                             all_qids.add(qid)
                file_to_qids[mid] = p180
        
        # Batch fetch Labels from Wikidata
        qid_labels = {}
        if all_qids:
            q_list = list(all_qids)
            for i in range(0, len(q_list), 50):
                chunk = q_list[i:i+50]
                wd_params = {
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": "labels",
                    "languages": "en",
                    "format": "json"
                }
                wd_resp = requests.get(WIKIDATA_API, params=wd_params, headers={"User-Agent": USER_AGENT}).json()
                if "entities" in wd_resp:
                    for qid, q_data in wd_resp["entities"].items():
                        qid_labels[qid] = q_data.get("labels", {}).get("en", {}).get("value", qid)

        # Enrich files
        for f in current_page_files:
            mid = f"M{f['pageid']}"
            if mid in file_to_qids:
                f["depicts"] = [
                    {"id": q, "label": qid_labels.get(q, q)}
                    for q in file_to_qids[mid]
                ]

    return jsonify({
        "results": current_page_files,
        "has_next": has_next,
        "page": page,
        "found_entity": {
            "id": cat_title,
            "label": cat_title,
            "description": "Wikimedia Commons Category"
        }
    })

@app.route("/api/file/<pageid>")
def file_details(pageid):
    """
    Get file details and SDC (Structured Data on Commons)
    """
    # Get M-ID (MediaInfo ID). Usually M + pageid for Commons files.
    # But let's fetch it properly via wbgetentities? 
    # Actually, simpler to look up by pageid directly on Commons API to get the M-ID concept or just use the pageid to find the entity.
    # SDC entities are conceptualized as 'M' + page_id.
    
    mid = f"M{pageid}"
    
    params = {
        "action": "wbgetentities",
        "ids": mid,
        "format": "json"
    }
    
    resp = requests.get(COMMONS_API, params=params, headers={"User-Agent": USER_AGENT}).json()
    
    if "entities" not in resp or mid not in resp["entities"]:
         return jsonify({"error": "Entity not found"}), 404
         
    entity = resp["entities"][mid]
    statements = entity.get("statements", {})
    
    depicts = []
    if "P180" in statements:
        p180_claims = statements["P180"]
        
        # Get all QIDs
        qids = []
        for claim in p180_claims:
            main_snak = claim.get("mainsnak", {})
            if main_snak.get("snaktype") == "value":
                datavalue = main_snak.get("datavalue", {})
                if datavalue.get("type") == "wikibase-entityid":
                    qids.append(datavalue["value"]["id"])
            
        # Bulk resolve QID labels from Wikidata
        if qids:
            chunk_size = 50
            labels = {}
            for i in range(0, len(qids), chunk_size):
                chunk = qids[i:i+chunk_size]
                wd_params = {
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": "labels",
                    "languages": "en",
                    "format": "json"
                }
                wd_resp = requests.get(WIKIDATA_API, params=wd_params, headers={"User-Agent": USER_AGENT}).json()
                if "entities" in wd_resp:
                     for qid, q_data in wd_resp["entities"].items():
                         labels[qid] = q_data.get("labels", {}).get("en", {}).get("value", qid)
            
            for qid in qids:
                depicts.append({
                    "id": qid,
                    "label": labels.get(qid, qid)
                })

    return jsonify({
        "mid": mid,
        "depicts": depicts
    })

@app.route("/api/wikidata_search")
def wikidata_search():
    query = request.args.get("q")
    if not query:
        return jsonify([])
        
    wd_params = {
        "action": "wbsearchentities",
        "search": query,
        "language": "en",
        "format": "json",
        "limit": 10
    }
    resp = requests.get(WIKIDATA_API, params=wd_params, headers={"User-Agent": USER_AGENT}).json()
    results = []
    if "search" in resp:
        for item in resp["search"]:
            results.append({
                "id": item["id"],
                "label": item.get("label", item["id"]),
                "description": item.get("description", "")
            })
    return jsonify(results)

@app.route("/api/add_claim", methods=["POST"])
def add_claim():
    """
    Adds a P180 statement to a MediaInfo entity.
    """
    data = request.json
    mid = data.get("mid")
    qid = data.get("qid")
    
    if not mid or not qid:
        return jsonify({"error": "Missing info"}), 400
        
    if not BOT_USERNAME or not BOT_PASSWORD:
        return jsonify({"error": "Server not configured with bot credentials"}), 500

    try:
        session = get_commons_session()
        csrf_token = get_csrf_token(session)
        
        # Determine value snak
        value = {
            "entity-type": "item",
            "numeric-id": int(qid.replace("Q", ""))
        }
        
        # https://www.mediawiki.org/wiki/Wikibase/API#wbcreateclaim
        params = {
            "action": "wbcreateclaim",
            "entity": mid,
            "property": "P180",
            "snaktype": "value",
            "value": str(jsonify(value).data, 'utf-8') if isinstance(value, str) else str(value).replace("'", '"'), # Tricky part, requests handles dicts usually but wb APIs can be finicky with JSON encoding of values
            "bot": 1,
            "token": csrf_token,
            "format": "json"
        }
        
        # Actually requests handles json value encoding if passed as string
        # Correct format for 'value' in wbcreateclaim is a JSON string stringifying the datavalue content object?
        # No, for 'value' parameter in `wbcreateclaim`:
        # "The value to set the snak to. JSON encoding of the value as expected by the datatype ... For 'wikibase-item': '{"entity-type":"item","numeric-id":1}'"
        
        import json
        json_value = json.dumps(value)
        
        post_data = {
            "action": "wbcreateclaim",
            "entity": mid,
            "property": "P180",
            "snaktype": "value",
            "value": json_value,
            "bot": 1,
            "token": csrf_token,
            "format": "json"
        }
        
        api_resp = session.post(COMMONS_API, data=post_data)
        resp_json = api_resp.json()
        
        if "error" in resp_json:
            return jsonify({"error": resp_json["error"]["info"]}), 400
            
        return jsonify({"success": True, "data": resp_json})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Local Gallery Routes ---

@app.route("/api/upload", methods=["POST"])
def upload_image():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    if file:
        try:
            original_fn, thumb_fn = process_image(file)
            
            new_image = ImageModel(
                filename=original_fn,
                thumbnail_filename=thumb_fn,
                title=request.form.get("title", file.filename)
            )
            db.session.add(new_image)
            db.session.commit()
            
            return jsonify({"success": True, "image": new_image.to_dict()}), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    return jsonify({"error": "Upload failed"}), 400

@app.route("/api/images", methods=["GET"])
def get_images():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    pagination = db.paginate(db.select(ImageModel), page=page, per_page=per_page)
    
    return jsonify({
        "images": [img.to_dict() for img in pagination.items],
        "meta": {
            "total_pages": pagination.pages,
            "current_page": page,
            "has_next": pagination.has_next,
            "total_items": pagination.total
        }
    })

@app.route('/static/images/<path:filename>')
def serve_static_image(filename):
    response = send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    # Cache for 1 year
    response.headers['Cache-Control'] = 'public, max-age=31536000'
    return response

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
