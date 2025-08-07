import yaml
import os
from transitions import Machine
from transitions.core import MachineError
from prompt_toolkit import prompt
from prompt_toolkit.completion import FuzzyWordCompleter
from prompt_toolkit.styles import Style

RULE_FILE = "rules.yaml"
COMMAND_FILE = "commands.yaml"

def load_rules():
    if not os.path.exists(RULE_FILE):
        save_rules({})
    with open(RULE_FILE, "r") as f:
        return yaml.safe_load(f) or {}

def save_rules(rules):
    with open(RULE_FILE, "w") as f:
        yaml.safe_dump(rules, f, sort_keys=False, default_flow_style=False)

def load_commands():
    if not os.path.exists(COMMAND_FILE):
        print(f"[ERROR] '{COMMAND_FILE}' not found.")
        exit(1)
    with open(COMMAND_FILE, "r") as f:
        return yaml.safe_load(f) or []

def build_machine(rules, initial_state="idle"):
    states = set(rules.keys())
    for ops in rules.values():
        for op_info in ops.values():
            states.update(op_info['next_states'])

    transitions = []
    for src, op_map in rules.items():
        for op_name, op_info in op_map.items():
            if op_info['next_states']:
                dest = op_info['next_states'][0]
                transitions.append({'trigger': op_name, 'source': src, 'dest': dest})

    class NANDController:
        pass

    controller = NANDController()
    machine = Machine(model=controller, states=list(states), transitions=transitions, initial=initial_state)
    return controller, machine, list(states)

def print_status(controller, pending_transitions):
    print("=" * 40)
    print(f"Current State: {controller.state}")
    if pending_transitions:
        print(f"Next State(s): {pending_transitions}")
    else:
        print("Next State(s): None")
    print("=" * 40)

def ensure_state_has_all_commands(state, rules, commands):
    if state not in rules:
        rules[state] = {}

    updated = False
    for cmd in commands:
        if cmd not in rules[state]:
            rules[state][cmd] = {'next_states': [], 'probability': ""}
            updated = True

    if updated:
        print(f"[INFO] Placeholder added for missing commands in state '{state}'")
        save_rules(rules)

def interactive_loop():
    rules = load_rules()
    commands = load_commands()

    # 최초 상태 idle 보장
    ensure_state_has_all_commands("idle", rules, commands)

    controller, machine, all_states = build_machine(rules)
    pending_transitions = []

    base_commands = ["advance", "exit"]
    style = Style.from_dict({"prompt": "#00ffff bold"})

    print("NAND Transition Interactive Editor")
    print("Type 'advance' to progress time, 'exit' to quit.\n")

    while True:
        print_status(controller, pending_transitions)

        current_state = controller.state
        cmd_completer = FuzzyWordCompleter(commands + base_commands)
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

        # 현재 상태가 rules에 없거나 명령어 누락된 경우 placeholder 채우기
        ensure_state_has_all_commands(current_state, rules, commands)

        # 해당 command가 placeholder일 경우, 사용자에게 정보 입력 받기
        rule_entry = rules[current_state][cmd]
        if not rule_entry['next_states']:
            print(f"Defining rule for '{cmd}' in state '{current_state}'")

            _, _, all_states = build_machine(rules, initial_state=current_state)
            state_completer = FuzzyWordCompleter(all_states)
            next_raw = prompt("Enter comma-separated next_states: ", completer=state_completer, style=style).strip()
            next_states = [s.strip() for s in next_raw.split(",") if s.strip()]

            try:
                prob = float(prompt("Enter probability (0.0 ~ 1.0): ", style=style).strip())
            except ValueError:
                prob = 1.0
                print("Invalid input. Using default probability = 1.0")

            rules[current_state][cmd] = {
                'next_states': next_states,
                'probability': prob
            }
            save_rules(rules)
            controller, machine, all_states = build_machine(rules, initial_state=current_state)

        # 명령 실행
        if hasattr(controller, cmd):
            try:
                prev_state = controller.state
                getattr(controller, cmd)()

                if prev_state == controller.state:
                    print(f"Command executed: state remained as '{controller.state}'")
                else:
                    print(f"Command executed: state changed → {controller.state}")

                    # 전이된 상태에 대해 command 누락 검사
                    ensure_state_has_all_commands(controller.state, rules, commands)

                next_states = rules[prev_state][cmd]['next_states']
                if len(next_states) > 1:
                    pending_transitions = next_states[1:]
                    print(f"Scheduled future transitions: {pending_transitions}")
                else:
                    pending_transitions = []

            except MachineError as e:
                print("[❌ ERROR] MachineError:", e)
                print("[DEBUG] Available states:", machine.states)
                print("[DEBUG] Current state:", controller.state)
                print("[DEBUG] Attempted command:", cmd)
                print("[DEBUG] Registered triggers:", machine.get_triggers(controller.state))
        else:
            print(f"Command '{cmd}' is not executable in current state.")

if __name__ == "__main__":
    interactive_loop()