import type { TaskViewMode } from "./types";

interface Props {
  mode: TaskViewMode;
  onChange: (mode: TaskViewMode) => void;
}

export default function TaskModeSwitcher({ mode, onChange }: Props) {
  return (
    <div className="task-mode-switcher" role="tablist" aria-label="Task breakdown mode">
      <button
        type="button"
        role="tab"
        aria-selected={mode === "authoring"}
        className={`task-mode-switcher__btn${mode === "authoring" ? " task-mode-switcher__btn--active" : ""}`}
        onClick={() => onChange("authoring")}
      >
        Authoring Template
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={mode === "execution"}
        className={`task-mode-switcher__btn${mode === "execution" ? " task-mode-switcher__btn--active" : ""}`}
        onClick={() => onChange("execution")}
      >
        Execution Cockpit
      </button>
    </div>
  );
}
