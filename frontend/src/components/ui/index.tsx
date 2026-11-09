import {
  type ReactNode,
  type ButtonHTMLAttributes,
  type CSSProperties,
} from "react";

export function Button({
  variant = "default",
  size,
  loading,
  disabled,
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost";
  size?: "lg";
  loading?: boolean;
}) {
  const cls = [
    "btn",
    variant === "primary" ? "btn-primary" : "",
    variant === "ghost" ? "btn-ghost" : "",
    size === "lg" ? "btn-lg" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button className={cls} disabled={disabled || loading} {...rest}>
      {loading && <Spinner small />}
      {children}
    </button>
  );
}

export function Card({
  children,
  className = "",
  onClick,
  style,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  style?: CSSProperties;
}) {
  return (
    <div
      className={`card ${onClick ? "card-click" : ""} ${className}`}
      onClick={onClick}
      style={style}
    >
      {children}
    </div>
  );
}

export function Toggle({
  on,
  onChange,
  label,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <div className="switch-row">
      <span style={{ fontWeight: 600, fontSize: "0.94rem" }}>{label}</span>
      <div
        role="switch"
        aria-checked={on}
        aria-label={label}
        className={`toggle ${on ? "on" : ""}`}
        onClick={() => onChange(!on)}
      />
    </div>
  );
}

export function Badge({
  status,
}: {
  status: "queued" | "processing" | "completed" | "failed";
}) {
  const dots = {
    completed: <span style={{ color: "var(--success)" }}>●</span>,
    processing: <span className="pulse" style={{ color: "var(--accent)" }}>●</span>,
    queued: <span style={{ color: "var(--warning)" }}>●</span>,
    failed: <span style={{ color: "var(--danger)" }}>●</span>,
  };
  return (
    <span className={`badge badge-${status}`}>
      {dots[status]}
      {status}
    </span>
  );
}

export function Spinner({ small }: { small?: boolean }) {
  return (
    <span
      className="spinner"
      style={small ? { width: 16, height: 16, borderWidth: 2 } : undefined}
    />
  );
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div className="progress-track">
      <div
        className="progress-fill"
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label className="field-label">{label}</label>
      {children}
    </div>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="segmented">
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          className={`seg-btn ${value === opt ? "active" : ""}`}
          onClick={() => onChange(opt)}
        >
          {opt}
        </button>
      ))}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  hint,
}: {
  icon: string;
  title: string;
  hint?: string;
}) {
  return (
    <div className="empty">
      <div style={{ fontSize: 34, marginBottom: 8 }}>{icon}</div>
      <div style={{ fontWeight: 700, color: "var(--text-2)" }}>{title}</div>
      {hint && (
        <div style={{ marginTop: 4, fontSize: "0.88rem" }}>{hint}</div>
      )}
    </div>
  );
}