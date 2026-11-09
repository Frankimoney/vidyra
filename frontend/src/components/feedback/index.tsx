import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";

/* ------------------------------------------------------------------ */
/* Toast                                                               */
/* ------------------------------------------------------------------ */

type ToastTone = "info" | "success" | "error";
interface ToastMsg {
  id: number;
  message: string;
  tone: ToastTone;
}

const ToastCtx = createContext<(message: string, tone?: ToastTone) => void>(
  () => undefined
);

export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastMsg[]>([]);
  const nextId = useRef(1);

  const toast = useCallback((message: string, tone: ToastTone = "info") => {
    const id = nextId.current++;
    setItems((prev) => [...prev, { id, message, tone }]);
    window.setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id));
    }, 3200);
  }, []);

  const colors: Record<ToastTone, string> = {
    info: "var(--accent)",
    success: "var(--success)",
    error: "var(--danger)",
  };

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div
        style={{
          position: "fixed",
          top: 14,
          left: "50%",
          transform: "translateX(-50%)",
          width: "min(92vw, 440px)",
          zIndex: 200,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          pointerEvents: "none",
        }}
      >
        {items.map((t) => (
          <div
            key={t.id}
            className="glass fade-up"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "12px 14px",
              borderLeft: `3px solid ${colors[t.tone]}`,
              fontSize: "0.9rem",
              fontWeight: 600,
            }}
          >
            <span style={{ color: colors[t.tone] }}>
              {t.tone === "success" ? "✓" : t.tone === "error" ? "⚠" : "ℹ"}
            </span>
            {t.message}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ------------------------------------------------------------------ */
/* Confirm                                                             */
/* ------------------------------------------------------------------ */

interface ConfirmOptions {
  title?: string;
  message?: string;
  confirmLabel?: string;
  danger?: boolean;
}

const ConfirmCtx = createContext<(opts: ConfirmOptions) => Promise<boolean>>(
  async () => false
);

export const useConfirm = () => useContext(ConfirmCtx);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOptions & { resolve: (v: boolean) => void }) | null>(
    null
  );

  const ask = useCallback((opts: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => setState({ resolve, ...opts }));
  }, []);

  function close(result: boolean) {
    setState((s) => {
      if (s) s.resolve(result);
      return null;
    });
  }

  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      {state && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.6)",
            zIndex: 300,
            display: "grid",
            placeItems: "center",
            padding: 24,
          }}
          onClick={() => close(false)}
        >
          <div className="card fade-up" style={{ width: "100%", maxWidth: 360 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ fontWeight: 800, fontSize: "1.05rem" }}>
              {state.title || "Are you sure?"}
            </div>
            {state.message && (
              <p className="muted" style={{ margin: "8px 0 18px", fontSize: "0.9rem" }}>
                {state.message}
              </p>
            )}
            <div className="btn-row">
              <button type="button" className="btn" onClick={() => close(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                style={state.danger ? { background: "var(--danger)", boxShadow: "none", border: "none" } : undefined}
                onClick={() => close(true)}
              >
                {state.confirmLabel || "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmCtx.Provider>
  );
}

/* ------------------------------------------------------------------ */
/* Clipboard helper (secure-context + fallback)                        */
/* ------------------------------------------------------------------ */

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      return true;
    } catch {
      return false;
    }
  }
}