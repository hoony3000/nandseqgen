import streamlit as st
import pandas as pd
import yaml
from pyvis.network import Network

RULE_FILE = "rules.yaml"
COMMAND_FILE = "commands.yaml"

def load_yaml(file):
    with open(file, "r") as f:
        return yaml.safe_load(f) or {}

def save_yaml(file, data):
    with open(file, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)

def build_dataframe(rules, commands):
    data = []
    for state in rules:
        for cmd in commands:
            entry = rules[state].get(cmd, {"next_states": [], "probability": ""})
            data.append({
                "state": state,
                "command": cmd,
                "next_states": ",".join(entry["next_states"]),
                "probability": entry["probability"]
            })
    return pd.DataFrame(data)

def save_rules_from_df(df):
    result = {}
    for row in df.to_dict(orient="records"):
        s, c = row["state"], row["command"]
        ns = [x.strip() for x in row["next_states"].split(",") if x.strip()]
        prob = row["probability"]
        result.setdefault(s, {})[c] = {"next_states": ns, "probability": prob}
    save_yaml(RULE_FILE, result)

def render_graph(df):
    net = Network(height="600px", width="100%", directed=True)
    for row in df.itertuples():
        src = row.state
        cmd = row.command
        for tgt in row.next_states.split(","):
            tgt = tgt.strip()
            if tgt:
                net.add_node(src)
                net.add_node(tgt)
                net.add_edge(src, tgt, label=cmd)
    net.save_graph("graph.html")
    return "graph.html"

# Streamlit UI
st.title("NAND 상태 전이 편집기 (with 시각화)")
rules = load_yaml(RULE_FILE)
commands = load_yaml(COMMAND_FILE)
df = build_dataframe(rules, commands)

# 🔍 검색 입력창
query = st.text_input("🔍 Search (state or command)")
if query:
    query = query.lower()
    df = df[df['state'].str.lower().str.contains(query) | df['command'].str.lower().str.contains(query)]

# 테이블 편집기
edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)

# 저장 버튼
if st.button("💾 Save rules.yaml"):
    save_rules_from_df(edited_df)
    st.success("Saved to rules.yaml")

# 그래프 시각화
st.subheader("🔍 상태 전이 그래프")
graph_file = render_graph(edited_df)
with open(graph_file, "r", encoding="utf-8") as f:
    html = f.read()
st.components.v1.html(html, height=640, scrolling=True)