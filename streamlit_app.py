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
        next_states = [s.strip() for s in row.next_states.split(",") if s.strip()]
        if not next_states:
            continue

        net.add_node(src)
        net.add_node(next_states[0])
        net.add_edge(src, next_states[0], label=cmd)

        for i in range(1, len(next_states)):
            net.add_node(next_states[i])
            net.add_edge(next_states[i - 1], next_states[i], label="⏱")

    net.save_graph("graph.html")
    return "graph.html"

# Streamlit UI
st.title("NAND 상태 전이 편집기 (with 시각화)")
rules = load_yaml(RULE_FILE)
commands = load_yaml(COMMAND_FILE)
df = build_dataframe(rules, commands)

query = st.text_input("🔍 Search (state or command)")
if query:
    query = query.lower()
    df = df[df['state'].str.lower().str.contains(query) | df['command'].str.lower().str.contains(query)]

edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)

if st.button("💾 Save rules.yaml"):
    save_rules_from_df(edited_df)
    st.success("Saved to rules.yaml")

st.subheader("🔍 상태 전이 그래프")
graph_file = render_graph(edited_df)
with open(graph_file, "r", encoding="utf-8") as f:
    html = f.read()
st.components.v1.html(html, height=640, scrolling=True)
