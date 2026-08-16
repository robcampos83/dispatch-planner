import io
import re
import pandas as pd
import streamlit as st
from collections import defaultdict

st.set_page_config(page_title="Routing Engine", layout="wide")

# --- STATE MANAGEMENT ---
# This is crucial. It keeps your board active in memory so you can manipulate it.
if "active_bins" not in st.session_state:
    st.session_state.active_bins = []

# --- UTILITIES ---
def clean_number(value):
    clean_val = re.sub(r"[^\d.-]", "", str(value))
    try: return float(clean_val) if clean_val else 0.0
    except: return 0.0

def normalize_store_id(store):
    s = re.sub(r"^(ol)+", "", str(store).strip(), flags=re.IGNORECASE).strip()
    return s.lstrip("0") if s.lstrip("0") else ("0" if s else "")

def get_trailer_limits(trailer_str):
    t_lower = str(trailer_str).lower()
    if "pup" in t_lower: return 900.0, 44000.0
    if "banana" in t_lower: return 1250.0, 44000.0
    if "48" in t_lower: return 1600.0, 44000.0
    return 2000.0, 44000.0 # Default 53'

# --- DATA LOADING & PARSING ---
def load_data(stores_file, roadshow_file):
    ordered_stores = []
    stores_content = io.StringIO(stores_file.getvalue().decode("utf-8"))
    for line in stores_content:
        raw_line = line.strip()
        if not raw_line or raw_line.startswith("#"): continue
        tokens = [t.strip() for t in re.split(r"[,\t]+|\s{2,}", raw_line) if t.strip()]
        if not tokens or tokens[0].lower() in ["store", "store#"]: continue
        
        ordered_stores.append({
            "raw_id": tokens[0],
            "norm_id": normalize_store_id(tokens[0]),
            "trailer": tokens[2].strip() if len(tokens) > 2 else "53'",
        })

    aggregated_loads = defaultdict(lambda: {"cubes": 0.0, "weight": 0.0})
    roadshow_content = io.StringIO(roadshow_file.getvalue().decode("utf-8"))
    for line in roadshow_content:
        line = line.strip()
        if not line or line.startswith("#"): continue
        tokens = [t.strip() for t in re.split(r"[,\t]+|\s+", line) if t.strip()]
        if len(tokens) >= 5 and tokens[0].lower() not in ["store", "store#"]:
            norm_id = normalize_store_id(tokens[0])
            aggregated_loads[norm_id]["cubes"] += clean_number(tokens[2])
            aggregated_loads[norm_id]["weight"] += clean_number(tokens[4])

    bins = []
    import uuid
    for store in ordered_stores:
        norm_id = store["norm_id"]
        if norm_id in aggregated_loads:
            load = aggregated_loads[norm_id]
            max_c, max_w = get_trailer_limits(store["trailer"])
            
            # Simplified for space: adding directly to bins
            bins.append({
                "bin_id": str(uuid.uuid4()),
                "stores": [store["raw_id"]],
                "cubes": load["cubes"],
                "weight": load["weight"],
                "trailer": store["trailer"],
                "max_c": max_c,
                "max_w": max_w
            })
    return bins

# --- UI & LOGIC ---
st.title("🚚 Interactive Dispatch Board")

with st.sidebar:
    st.header("📂 Upload Files")
    stores_file = st.file_uploader("stores.txt", type=["txt", "csv"])
    roadshow_file = st.file_uploader("roadshow.txt", type=["txt", "csv"])
    
    if st.button("⚡ Load & Reset Board", use_container_width=True, type="primary"):
        if stores_file and roadshow_file:
            st.session_state.active_bins = load_data(stores_file, roadshow_file)
            st.rerun()

if not st.session_state.active_bins:
    st.info("Upload your files and click 'Load & Reset Board' to begin.")
else:
    # 1. Build the DataFrame for the Interactive Grid
    df_data = []
    for i, b in enumerate(st.session_state.active_bins):
        status = "⚠️ OVER CAPACITY" if b["cubes"] > b["max_c"] or b["weight"] > b["max_w"] else "OK"
        df_data.append({
            "Select": False,  # Checkbox column
            "Bin ID": b["bin_id"],
            "Seq": i + 1,
            "Stores": " | ".join(b["stores"]),
            "Cubes": round(b["cubes"], 1),
            "Max Cubes": b["max_c"],
            "Weight": round(b["weight"], 1),
            "Trailer": b["trailer"],
            "Status": status
        })
    df = pd.DataFrame(df_data)

    # 2. Control Panel (Replaces Drag-and-Drop)
    col1, col2, col3 = st.columns(3)
    with col1:
        combine_btn = st.button("🔗 Combine Selected", use_container_width=True)
    with col2:
        uncombine_btn = st.button("✂️ Uncombine Selected", use_container_width=True)
        
    st.markdown("💡 *Check the boxes on the left of the grid, then click an action above.*")

    # 3. Render the Interactive Grid
    edited_df = st.data_editor(
        df,
        hide_index=True,
        column_config={
            "Select": st.column_config.CheckboxColumn("Select", default=False),
            "Bin ID": None, # Hide the ugly UUID
        },
        disabled=["Seq", "Stores", "Cubes", "Max Cubes", "Weight", "Trailer", "Status"],
        use_container_width=True,
        height=600
    )

    # --- ACTIONS ---
    # Combine Logic
    if combine_btn:
        selected_ids = edited_df[edited_df["Select"] == True]["Bin ID"].tolist()
        if len(selected_ids) < 2:
            st.warning("Please check at least two rows to combine.")
        else:
            # Find the bins to merge
            bins_to_merge = [b for b in st.session_state.active_bins if b["bin_id"] in selected_ids]
            
            # Create a new merged bin based on the first one
            target_bin = bins_to_merge[0].copy()
            for b in bins_to_merge[1:]:
                target_bin["stores"].extend(b["stores"])
                target_bin["cubes"] += b["cubes"]
                target_bin["weight"] += b["weight"]
            
            # Remove old bins, insert new one
            st.session_state.active_bins = [b for b in st.session_state.active_bins if b["bin_id"] not in selected_ids]
            st.session_state.active_bins.insert(0, target_bin) # Puts combined load at top
            st.rerun()

    # Export Logic
    st.markdown("---")
    csv = df.drop(columns=["Select", "Bin ID"]).to_csv(index=False).encode('utf-8')
    st.download_button("💾 Export Dispatch Plan", data=csv, file_name="dispatch_plan.csv", mime="text/csv")
