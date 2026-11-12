import { NavLink, Outlet } from "react-router-dom";

const tabs = [
  { to: "create", label: "Create" },
  { to: "scenes", label: "Scenes" },
  { to: "tools", label: "Tools" },
  { to: "cast", label: "Cast" },
];

export default function StudioLayout() {
  return (
    <div className="fade-up">
      <header style={{ marginBottom: 14 }}>
        <h2 style={{ margin: 0 }}>Studio</h2>
        <p className="faint" style={{ fontSize: "0.88rem", margin: "4px 0 0" }}>
          Make a film, animate a shot, or use a single AI tool.
        </p>
      </header>

      <nav className="segmented" style={{ marginBottom: 18 }} aria-label="Studio sections">
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            className={({ isActive }) => `seg-btn ${isActive ? "active" : ""}`}
            style={{ textDecoration: "none" }}
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>

      <Outlet />
    </div>
  );
}
