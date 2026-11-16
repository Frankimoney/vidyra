import { NavLink, Outlet } from "react-router-dom";
import { StudioQuotaMeter } from "../components/StudioQuotaMeter";

const tabs = [
  { to: "create", label: "Create", icon: "✨" },
  { to: "scenes", label: "Scenes", icon: "🎞️" },
  { to: "tools", label: "Tools", icon: "🧪" },
  { to: "cast", label: "Cast", icon: "🎭" },
];

export default function StudioLayout() {
  return (
    <div className="fade-up">
      <header
        className="spread"
        style={{ marginBottom: 16, gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: "1.55rem" }}>Studio</h2>
          <p className="faint" style={{ fontSize: "0.88rem", margin: "4px 0 0" }}>
            Direct a multi-scene film, build a timeline, or run a single AI tool.
          </p>
        </div>
        <StudioQuotaMeter />
      </header>

      <nav className="segmented" style={{ marginBottom: 20 }} aria-label="Studio sections">
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            className={({ isActive }) => `seg-btn ${isActive ? "active" : ""}`}
            style={{
              textDecoration: "none",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 7,
            }}
          >
            <span aria-hidden="true">{tab.icon}</span>
            <span>{tab.label}</span>
          </NavLink>
        ))}
      </nav>

      <Outlet />
    </div>
  );
}
