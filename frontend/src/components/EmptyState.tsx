interface EmptyStateProps {
  glyph?: string;
  title: string;
  sub?: string;
}

export function EmptyState({ glyph = "≋", title, sub }: EmptyStateProps) {
  return (
    <div className="df-empty df-fade-in">
      <div className="df-empty__glyph">{glyph}</div>
      <div className="df-empty__title">{title}</div>
      {sub ? <div className="df-empty__sub">{sub}</div> : null}
    </div>
  );
}
