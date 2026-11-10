import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

export function BoltIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
      <path d="M13 2 4.5 13.5H11L9.5 22 19 10h-6.5L13 2Z" />
    </svg>
  );
}

type NavItem = {
  to: string;
  label: string;
  more?: boolean;
  icon: React.ReactNode;
};

const items: NavItem[] = [
  {
    to: "/",
    label: "Home",
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 10.5 12 3l9 7.5" />
        <path d="M5 9.5V21h14V9.5" />
        <path d="M9 21v-6h6v6" />
      </svg>
    ),
  },
  {
    to: "/new",
    label: "New",
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3v3" />
        <path d="M12 18v3" />
        <path d="M3 12h3" />
        <path d="M18 12h3" />
        <path d="m5.6 5.6 2.1 2.1" />
        <path d="m16.3 16.3 2.1 2.1" />
        <path d="m18.4 5.6-2.1 2.1" />
        <path d="m7.7 16.3-2.1 2.1" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    ),
  },
  {
    to: "/characters",
    label: "Cast",
    /* Mobile keeps only Home / Create / AI Studio plus More — five slots in a
       480px bar left no room to read the labels. The desktop top nav lists
       every destination regardless of this flag, so Cast stays one tap away
       there. */
    more: true,
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="9" cy="8" r="3.2" />
        <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
        <circle cx="17" cy="9.5" r="2.4" />
        <path d="M15.2 20a4.4 4.4 0 0 1 5.3-3.6" />
      </svg>
    ),
  },
  {
    to: "/studio",
    label: "AI Studio",
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="18" height="12" rx="2" />
        <path d="M8 20h8" />
        <path d="M12 16v4" />
        <path d="M9 9.5 12 12l3-2.5" />
      </svg>
    ),
  },
  {
    to: "/film",
    label: "Film",
    more: true,
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2" y="5" width="20" height="14" rx="2" />
        <path d="M7 5v14" />
        <path d="M17 5v14" />
        <path d="M2 10h5" />
        <path d="M2 15h5" />
        <path d="M17 10h5" />
        <path d="M17 15h5" />
      </svg>
    ),
  },
  {
    to: "/editor",
    label: "Editor",
    more: true,
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="7" height="7" rx="1.5" />
        <rect x="14" y="4" width="7" height="7" rx="1.5" />
        <rect x="3" y="13" width="7" height="7" rx="1.5" />
        <rect x="14" y="13" width="7" height="7" rx="1.5" />
      </svg>
    ),
  },
  {
    to: "/series",
    label: "Series",
    more: true,
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 2l4 4-4 4" />
        <path d="M3 11V9a4 4 0 0 1 4-4h14" />
        <path d="M7 22l-4-4 4-4" />
        <path d="M21 13v2a4 4 0 0 1-4 4H3" />
      </svg>
    ),
  },
  {
    to: "/workspace",
    label: "Workspace",
    more: true,
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 21V7l8-4 8 4v14" />
        <path d="M9 21v-6h6v6" />
        <path d="M4 11h16" />
      </svg>
    ),
  },
  {
    to: "/videos",
    label: "My Videos",
    more: true,
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2" y="4" width="20" height="16" rx="3" />
        <path d="m10 9 5 3-5 3V9Z" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  {
    to: "/profile",
    label: "Account",
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="9" r="3.5" />
        <path d="M5 20c1.5-3.2 4-5 7-5s5.5 1.8 7 5" />
      </svg>
    ),
  },
];

const core = items.filter((it) => !it.more);
const extra = items.filter((it) => it.more);

export function TopNav() {
  return (
    <nav className="top-nav">
      <div className="top-nav-inner">
        <NavLink to="/" className="logo" style={{ textDecoration: "none" }}>
          <BoltIcon size={20} />
          vi<span className="bolt">dyra</span>
        </NavLink>
        <div className="top-nav-links">
          {items.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              end={it.to === "/"}
              title={it.label}
              className={({ isActive }) => `top-link ${isActive ? "active" : ""}`}
            >
              {it.icon}
              <span className="top-link-label">{it.label}</span>
            </NavLink>
          ))}
        </div>
      </div>
    </nav>
  );
}

export function MoreSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <>
      {open && <div className="sheet-backdrop" onClick={onClose} />}
      <div className={`more-sheet ${open ? "open" : ""}`} role="dialog" aria-modal="true" aria-hidden={!open}>
        <div className="more-sheet-head">
          <span style={{ fontWeight: 800 }}>More</span>
          <button className="more-sheet-close" onClick={onClose} aria-label="Close menu">
            ✕
          </button>
        </div>
        <div>
          {extra.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              end={it.to === "/"}
              className={({ isActive }) => `sheet-item ${isActive ? "active" : ""}`}
              onClick={onClose}
            >
              {it.icon}
              <span>{it.label}</span>
            </NavLink>
          ))}
        </div>
      </div>
    </>
  );
}

export function BottomNav() {
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const moreActive = extra.some((it) => pathname === it.to);

  return (
    <>
      <MoreSheet open={open} onClose={() => setOpen(false)} />
      <nav className="bottom-nav">
        {core.map((it) => (
          <NavLink
            key={it.to}
            to={it.to}
            end={it.to === "/"}
            className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
          >
            {it.icon}
            <span>{it.label}</span>
          </NavLink>
        ))}
        <button
          type="button"
          className={`nav-item ${moreActive || open ? "active" : ""}`}
          onClick={() => setOpen((v) => !v)}
        >
          <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none" />
            <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
            <circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none" />
          </svg>
          <span>More</span>
        </button>
      </nav>
    </>
  );
}

export function AppLayout() {
  return (
    <div className="app-shell">
      <TopNav />
      <main className="app-main">
        <Outlet />
      </main>
      <BottomNav />
    </div>
  );
}