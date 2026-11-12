import { NavLink, Outlet } from "react-router-dom";

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
  icon: React.ReactNode;
};

const items: NavItem[] = [
  {
    to: "/studio",
    label: "Studio",
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
    to: "/library",
    label: "Library",
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2" y="4" width="20" height="16" rx="3" />
        <path d="m10 9 5 3-5 3V9Z" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  {
    to: "/workspace",
    label: "Workspace",
    icon: (
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 21V7l8-4 8 4v14" />
        <path d="M9 21v-6h6v6" />
        <path d="M4 11h16" />
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

export function TopNav() {
  return (
    <nav className="top-nav">
      <div className="top-nav-inner">
        <NavLink to="/studio" className="logo" style={{ textDecoration: "none" }}>
          <BoltIcon size={20} />
          vi<span className="bolt">dyra</span>
        </NavLink>
        <div className="top-nav-links">
          {items.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
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

export function BottomNav() {
  return (
    <nav className="bottom-nav">
      {items.map((it) => (
        <NavLink
          key={it.to}
          to={it.to}
          className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
        >
          {it.icon}
          <span>{it.label}</span>
        </NavLink>
      ))}
    </nav>
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
