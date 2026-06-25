import { NavLink } from "react-router-dom";

interface NavItem {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Query", icon: "▸", end: true },
  { to: "/results", label: "Results", icon: "▦" },
  { to: "/history", label: "History", icon: "⟳" },
];

export function Sidebar() {
  return (
    <aside className="df-sidebar">
      <div className="df-brand">
        <span className="df-brand__mark">deepferry</span>
        <span className="df-brand__tag">data ferry</span>
      </div>

      <nav className="df-nav">
        <div className="df-nav__label">Workspace</div>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              `df-nav__link${isActive ? " is-active" : ""}`
            }
          >
            <span className="df-nav__icon">{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="df-sidebar__footer">
        v0.1.0 · m3
        <br />
        MCP data access layer
      </div>
    </aside>
  );
}
