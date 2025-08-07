import yaml
import os
from transitions import Machine
from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.styles import Style

RULE_FILE = "rules.yaml"
COMMAND_FILE = "commands.yaml"

# ---------------------
# YAML load/save
# ---------------------
def load_rules():
    if not os.path.exists(RULE_FILE):
        save_rules({})
    with open(RULE_FILE, "r") as f:
        return yaml.safe_load(f) or {}

def save_rules(rules):
    with open(RULE_FILE, "w") as f:
        yaml.safe_dump(rules, f, sort_keys=False, default_flow_style=True)

def load_commands():
    if not os.path.exists(COMMAND_FILE):
        print(f"[ERROR] '{COMMAND_FILE}' not found. Please create it with a list of commands.")
        exit(1)
    with open(COMMAND_FILE, "r") as f:
        return yaml.safe_load(f) or []

# ---------------------
# State Machine Builder
# ---------------------
def build_machine(rules):
    states = set(rules.keys())
    for ops in rules.values():
        for op_info in ops.values():
            states.update(op_info['next_state'])

    transitions = []
    for src, op_map in rules.items():
        for op_name, op_info in op_map.items():
            dest = op_info['next_state'][0]
            transitions.append({
                'trigger': op_name,
                'source': src,
                'dest': dest
            })

    class NANDController:
        pass

    controller = NANDController()
    machine = Machine(model=controller, states=list(states), transitions=transitions, initial='idle')
    return controller, machine, list(states)

# ---------------------
# Display info
# ---------------------
def print_status(controller, pending_transitions):
    print("=" * 40)
    print(f"Current State: {controller.state}")
    if pending_transitions:
        print(f"Next State(s): {pending_transitions}")
    else:
        print("Next State(s): None")
    print("=" * 40)

# ---------------------
# Main interaction loop
# ---------------------
def interactive_loop():
    rules = load_rules()
    commands = load_commands()
    controller, machine, all_states = build_machine(rules)
    pending_transitions = []

    style = Style.from_dict({"prompt": "#00ffff bold"})

    print("NAND Transition Interactive Editor (with prompt_toolkit)")
    print("Type 'advance' to progress time, 'exit' to quit.\n")

    while True:
        print_status(controller, pending_transitions)

        defined_cmds = set(rules.get(controller.state, {}).keys())
        available_cmds = [cmd for cmd in commands if cmd not in defined_cmds]

        cmd_completer = WordCompleter(commands + ["advance", "exit"], ignore_case=True)

        cmd = prompt("> Enter command: ", completer=cmd_completer, style=style).strip()

        if not cmd:
            continue
        if cmd == "exit":
            print("Exiting.")
            break
        if cmd == "advance":
            if pending_transitions:
                controller.state = pending_transitions.pop(0)
                print(f"Time advanced → New state: {controller.state}")
            else:
                print("No scheduled transitions.")
            continue
        if cmd not in commands:
            print(f"'{cmd}' is not a valid command from commands.yaml.")
            continue

        current_state = controller.state

        if current_state not in rules:
            rules[current_state] = {}

        if cmd not in rules[current_state]:
            print(f"Command '{cmd}' not yet defined for state '{current_state}'")

            # 상태 자동완성
            state_completer = WordCompleter(all_states, ignore_case=True)
            next_raw = prompt("Enter comma-separated next_states: ", completer=state_completer, style=style).strip()
            next_states = [s.strip() for s in next_raw.split(",") if s.strip()]

            try:
                prob = float(prompt("Enter probability (0.0 ~ 1.0): ", style=style).strip())
            except ValueError:
                prob = 1.0
                print("Invalid input. Using default probability = 1.0")

            rules[current_state][cmd] = {
                'next_state': next_states,
                'probability': prob
            }

            save_rules(rules)
            controller, machine, all_states = build_machine(rules)
            pending_transitions = []
            print(f"Rule added: {current_state} + {cmd} → {next_states} (p={prob})")
            continue

        # 명령 실행
        op_info = rules[current_state][cmd]
        next_states = op_info.get('next_state', [])

        if hasattr(controller, cmd):
            getattr(controller, cmd)()
            print(f"Command success → New state: {controller.state}")
            if len(next_states) > 1:
                pending_transitions = next_states[1:]
                print(f"Scheduled future transitions: {pending_transitions}")
            else:
                pending_transitions = []
        else:
            print(f"Command '{cmd}' is defined but not executable in current state.")

# ---------------------
# Entry point
# ---------------------
if __name__ == "__main__":
    interactive_loop()