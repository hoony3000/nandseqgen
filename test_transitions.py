import yaml
from transitions import Machine
import os

RULE_FILE = "rules.yaml"

# ---------------------
# Rule file handling
# ---------------------
def load_rules():
    if not os.path.exists(RULE_FILE):
        print(f"[Info] No rule file found. Creating empty rule set at {RULE_FILE}")
        save_rules({})
    with open(RULE_FILE, "r") as f:
        return yaml.safe_load(f)

def save_rules(rules):
    with open(RULE_FILE, "w") as f:
        yaml.safe_dump(rules, f, sort_keys=False, default_flow_style=True)

# ---------------------
# State Machine Builder
# ---------------------
def build_machine(rules):
    states = set(rules.keys())
    for ops in rules.values():
        for op_info in ops.values():
            next_states = op_info['next_state']
            states.update(next_states)

    transitions = []
    for src, op_map in rules.items():
        for op_name, op_info in op_map.items():
            dest = op_info['next_state'][0]  # only the first is used for immediate transition
            transitions.append({
                'trigger': op_name,
                'source': src,
                'dest': dest
            })

    class NANDController:
        pass

    controller = NANDController()
    machine = Machine(model=controller, states=list(states), transitions=transitions, initial='idle')
    return controller, machine

# ---------------------
# Operation Display
# ---------------------
def print_available_ops(rules, current_state):
    if current_state not in rules:
        print("No operations defined for this state.")
        return
    print("\nAvailable operations:")
    for op, meta in rules[current_state].items():
        prob = meta.get('probability', 1.0)
        next_states = meta.get('next_state', [])
        print(f"  - {op} → {next_states} (p={prob})")
    print()

# ---------------------
# Interactive Loop
# ---------------------
def interactive_loop():
    rules = load_rules()
    controller, machine = build_machine(rules)
    pending_transitions = []  # Track time-based transitions

    print("NAND Transition Interactive Editor (with Time-based Transitions)")
    print("Type 'exit' to quit, 'advance' to progress time.\n")

    while True:
        print(f"\n[Current State] {controller.state}")
        if pending_transitions:
            print(f"Scheduled future transitions: {pending_transitions}")
        else:
            print("No scheduled future transitions.")

        print_available_ops(rules, controller.state)
        cmd = input("Enter NAND operation or 'advance': ").strip()

        if not cmd:
            continue  # Ignore empty input

        if cmd.lower() == 'exit':
            print("Exiting.")
            break

        if cmd.lower() == 'advance':
            if pending_transitions:
                next_state = pending_transitions.pop(0)
                controller.state = next_state
                print(f"Time advanced → New state: {controller.state}")
            else:
                print("No scheduled transitions.")
            continue

        if hasattr(controller, cmd):
            current_state = controller.state
            op_info = rules.get(current_state, {}).get(cmd, {})
            next_states = op_info.get('next_state', [])

            getattr(controller, cmd)()
            print(f"Command success → New state: {controller.state}")

            if len(next_states) > 1:
                pending_transitions = next_states[1:]
                print(f"Scheduled future transitions: {pending_transitions}")
            else:
                pending_transitions = []
        else:
            print(f"Invalid operation '{cmd}' in state '{controller.state}'.")

            choice = input("Would you like to define this transition? (y/n): ").strip().lower()
            if choice == 'y':
                next_raw = input("Enter comma-separated states (e.g., erasing,erase_done): ").strip()
                next_states = [s.strip() for s in next_raw.split(",") if s.strip()]
                try:
                    prob = float(input("Enter the probability of this operation (0.0 ~ 1.0): ").strip())
                except ValueError:
                    prob = 1.0
                    print("Invalid input. Defaulting probability to 1.0")

                if controller.state not in rules:
                    rules[controller.state] = {}

                rules[controller.state][cmd] = {
                    'next_state': next_states,
                    'probability': prob
                }

                save_rules(rules)
                controller, machine = build_machine(rules)
                pending_transitions = []
                print(f"Rule added: {controller.state} + {cmd} → {next_states} (p={prob})")
            else:
                print("Skipping update.")

# ---------------------
# Main Entry
# ---------------------
if __name__ == "__main__":
    interactive_loop()