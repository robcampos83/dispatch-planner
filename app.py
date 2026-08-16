import io
import re
from collections import defaultdict
import pandas as pd
import streamlit as st

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Routing - Multi-Commodity Dispatch Planner",
    page_icon="🚚",
    layout="wide",
)

st.title("🚚 Multi-Commodity Dispatch Planner")
st.markdown(
    "*Web-based Dispatch Optimizer for managing cubes, weight limits, and load combinations.*"
)


# --- CORE UTILITIES & PARSERS ---
def clean_number(value: str) -> float:
  """Safely extracts numeric values from formatted or raw strings."""
  clean_val = re.sub(r"[^\d.-]", "", str(value))
  try:
    return float(clean_val) if clean_val else 0.0
  except ValueError:
    return 0.0


def normalize_store_id(store: str) -> str:
  s = str(store).strip()
  s = re.sub(r"^(ol)+", "", s, flags=re.IGNORECASE).strip()
  clean_str = s.lstrip("0")
  return clean_str if clean_str else ("0" if s else "")


def get_next_ol_name(store_name: str) -> str:
  first_store = re.sub(
      r"\s*\|\s*bananas?", "", store_name.split("|")[0], flags=re.IGNORECASE
  ).strip()
  return f"ol{first_store}"


def parse_config_file(config_file_obj, commodity: str = "Grocery") -> dict:
  comm_key = commodity.lower().strip()
  allow_banana = comm_key == "grocery"

  config_data = {
      "cubes": {"pup": 900.0, "48'": 1600.0, "53'": 2000.0},
      "max_weight": 44000.0,
  }
  if allow_banana:
    config_data["cubes"]["banana"] = 1250.0
    config_data["cubes"]["bananas"] = 1250.0

  if config_file_obj is None:
    return config_data

  try:
    stringio = io.StringIO(config_file_obj.getvalue().decode("utf-8"))
    current_section = None
    for line in stringio:
      line = line.strip()
      if not line or line.startswith("#"):
        continue

      section_match = re.match(r"^([\w' ]+):$", line)
      if section_match:
        current_section = section_match.group(1).strip().lower()
        continue

      sub_match = re.match(r"^([\w' ]+)[:\s]+([\d,.]+)", line)
      if sub_match:
        key = sub_match.group(1).strip().lower()
        val = clean_number(sub_match.group(2))
        if current_section == "max weight" or "weight" in key:
          if comm_key in key or current_section == "max weight":
            config_data["max_weight"] = val
        elif current_section:
          if comm_key in key or ("grocer" in comm_key and "grocer" in key):
            config_data["cubes"][current_section] = val
  except Exception:
    pass

  return config_data


def get_trailer_limits(
    trailer_str: str, config: dict, allow_banana: bool = True
) -> tuple:
  tokens = [t.strip().lower() for t in re.split(r"[|/]+", trailer_str) if t.strip()]
  if not tokens:
    tokens = ["53'"]

  cube_limits = []
  for t in tokens:
    if "pup" in t:
      cube_limits.append(config["cubes"].get("pup", 900.0))
    elif "banana" in t and allow_banana:
      cube_limits.append(config["cubes"].get("banana", 1250.0))
    elif "48" in t:
      cube_limits.append(config["cubes"].get("48'", 1600.0))
    elif "53" in t:
      cube_limits.append(config["cubes"].get("53'", 2000.0))
    else:
      cube_limits.append(config["cubes"].get("53'", 2000.0))

  max_cube = (
      min(cube_limits) if cube_limits else config["cubes"].get("53'", 2000.0)
  )
  max_weight = config.get("max_weight", 44000.0)
  return max_cube, max_weight


def parse_restrictions(tokens: list, raw_line: str, commodity: str) -> dict:
  trailer = tokens[2].strip() if len(tokens) > 2 else "53'"
  raw_curfew = tokens[3].strip().upper() if len(tokens) > 3 else "N"
  curfew_disp = "C" if raw_curfew == "Y" else ""
  raw_dh = tokens[4].strip() if len(tokens) > 4 else "0"

  comm = commodity.lower().strip()
  if comm in ["perishable", "frozen"]:
    dh_disp = "D&H" if raw_dh == "1" else ""
    if "banana" in trailer.lower():
      trailer = "53'"
  else:
    dh_disp = "D&H" if raw_dh in ["1", "2"] else ""

  shift = "AM"
  for t in tokens:
    tu = t.strip().upper()
    if tu in ["PM", "P.M.", "NIGHT", "EVENING"]:
      shift = "PM"
      break
    elif tu in ["AM", "A.M.", "MORNING"]:
      shift = "AM"
      break
  else:
    if re.search(r"\bPM\b|\bP\.M\.\b", raw_line, re.IGNORECASE):
      shift = "PM"

  return {
      "trailer": trailer,
      "curfew": curfew_disp,
      "dh": dh_disp,
      "has_curfew": raw_curfew == "Y",
      "shift": shift,
  }


# --- SIDEBAR CONFIGURATION & FILE UPLOADS ---
st.sidebar.header("⚙️ Dispatch Controls")
commodity_mode = st.sidebar.selectbox(
    "Active Commodity", ["Grocery", "Perishable", "Frozen"]
)
allow_banana = commodity_mode.lower() == "grocery"

st.sidebar.subheader("📂 Source Files")
config_file = st.sidebar.file_uploader(
    "Upload config.txt (Optional)", type=["txt"]
)
stores_file = st.sidebar.file_uploader(
    "Upload stores.txt", type=["txt", "csv"]
)

roadshow_default_name = (
    "roadshow_perish.txt"
    if commodity_mode == "Perishable"
    else ("roadshow_frozen.txt" if commodity_mode == "Frozen" else "roadshow_grocery.txt")
)
roadshow_file = st.sidebar.file_uploader(
    f"Upload {roadshow_default_name}", type=["txt", "csv"]
)

# --- MAIN EXECUTION LOGIC ---
if stores_file and roadshow_file:
  config_data = parse_config_file(config_file, commodity_mode)

  # 1. Parse Stores Master
  ordered_stores = []
  stores_content = io.StringIO(stores_file.getvalue().decode("utf-8"))
  for line in stores_content:
    raw_line = line.strip()
    if not raw_line or raw_line.startswith("#"):
      continue
    tokens = [
        t.strip() for t in re.split(r"[,\t]+|\s{2,}", raw_line) if t.strip()
    ]
    if not tokens or tokens[0].lower() in ["store", "store#", "store_id", "st#"]:
      continue

    raw_id = tokens[0]
    norm_id = normalize_store_id(raw_id)
    restrictions = parse_restrictions(tokens, raw_line, commodity_mode)
    ordered_stores.append({
        "raw_id": raw_id,
        "norm_id": norm_id,
        "trailer": restrictions["trailer"],
        "curfew": restrictions["curfew"],
        "dh": restrictions["dh"],
        "has_curfew": restrictions["has_curfew"],
        "shift": restrictions["shift"],
    })

  # 2. Parse Roadshow Data
  aggregated_loads = defaultdict(
      lambda: {"cubes": 0.0, "weight": 0.0, "count": 0}
  )
  roadshow_content = io.StringIO(roadshow_file.getvalue().decode("utf-8"))
  for line in roadshow_content:
    line = line.strip()
    if not line or line.startswith("#"):
      continue
    tokens = [t.strip() for t in re.split(r"[,\t]+|\s+", line) if t.strip()]
    if not tokens or tokens[0].lower() in ["store", "store#", "store_id", "st#"]:
      continue
    if len(tokens) >= 5:
      raw_id = tokens[0]
      norm_id = normalize_store_id(raw_id)
      cubes = clean_number(tokens[2])
      weight = clean_number(tokens[4])
      aggregated_loads[norm_id]["cubes"] += cubes
      aggregated_loads[norm_id]["weight"] += weight
      aggregated_loads[norm_id]["count"] += 1

  # 3. Process and Build Dispatch Rows (Handling Overloads/Splits)
  processed_rows = []
  row_counter = 1

  for store in ordered_stores:
    norm_id = store["norm_id"]
    if norm_id in aggregated_loads:
      load = aggregated_loads[norm_id]
      max_cube, max_weight = get_trailer_limits(
          store["trailer"], config_data, allow_banana
      )

      curr_cubes = load["cubes"]
      curr_weight = load["weight"]
      curr_name = store["raw_id"]
      shift = store["shift"]

      is_overload = curr_cubes > max_cube or curr_weight > max_weight

      if is_overload:
        while curr_cubes > max_cube or curr_weight > max_weight:
          alloc_cubes = min(curr_cubes, max_cube)
          alloc_weight = min(curr_weight, max_weight)

          processed_rows.append({
              "Shift": shift,
              "Seq": row_counter,
              "Store(s)": curr_name,
              "Total Cubes": round(alloc_cubes, 1),
              "Max Cubes": max_cube,
              "Total Weight": round(alloc_weight, 1),
              "Max Weight": max_weight,
              "Loads": load["count"] if curr_name == store["raw_id"] else 1,
              "Trailer": store["trailer"],
              "Curfew": store["curfew"],
              "Drop/Hook": store["dh"],
              "Status": "MAX LOAD (SPLIT)",
          })
          row_counter += 1
          curr_cubes -= alloc_cubes
          curr_weight -= alloc_weight
          curr_name = get_next_ol_name(curr_name)

        if curr_cubes > 0 or curr_weight > 0:
          processed_rows.append({
              "Shift": shift,
              "Seq": row_counter,
              "Store(s)": curr_name,
              "Total Cubes": round(curr_cubes, 1),
              "Max Cubes": max_cube,
              "Total Weight": round(curr_weight, 1),
              "Max Weight": max_weight,
              "Loads": 1,
              "Trailer": store["trailer"],
              "Curfew": store["curfew"],
              "Drop/Hook": store["dh"],
              "Status": "⚠️ OVERLOAD (OL)",
          })
          row_counter += 1
      else:
        processed_rows.append({
            "Shift": shift,
            "Seq": row_counter,
            "Store(s)": store["raw_id"],
            "Total Cubes": round(load["cubes"], 1),
            "Max Cubes": max_cube,
            "Total Weight": round(load["weight"], 1),
            "Max Weight": max_weight,
            "Loads": load["count"],
            "Trailer": store["trailer"],
            "Curfew": store["curfew"],
            "Drop/Hook": store["dh"],
            "Status": "OK",
        })
        row_counter += 1

  # Convert to Pandas DataFrame for interactive viewing
  df = pd.DataFrame(processed_rows)

  if commodity_mode.lower() == "frozen":
    st.subheader("🌙 Frozen — PM Shift Schedule")
    pm_df = df[df["Shift"] == "PM"].drop(columns=["Shift"])
    st.dataframe(pm_df, use_container_width=True, hide_index=True)

    st.subheader("🌅 Frozen — AM Shift Schedule")
    am_df = df[df["Shift"] == "AM"].drop(columns=["Shift"])
    st.dataframe(am_df, use_container_width=True, hide_index=True)
  else:
    st.subheader(f"📋 {commodity_mode} Dispatch Schedule")
    st.dataframe(
        df.drop(columns=["Shift"]), use_container_width=True, hide_index=True
    )

  # --- EXPORT BUTTON ---
  st.sidebar.markdown("---")
  csv_data = df.to_csv(index=False).encode("utf-8")
  st.sidebar.download_button(
      label="💾 Export Plan to CSV",
      data=csv_data,
      file_name=f"{commodity_mode.lower()}_dispatch_plan.csv",
      mime="text/csv",
  )

else:
  st.info(
      "👈 Please upload your **stores.txt** and corresponding **roadshow file**"
      " using the sidebar to generate the dispatch routing plan."
  )