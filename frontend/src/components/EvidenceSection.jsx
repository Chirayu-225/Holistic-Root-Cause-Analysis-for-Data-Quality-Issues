export default function EvidenceSection({ number, title, subtitle, children }) {
  return (
    <section className="evidence">
      <div className="evidence__rail">
        <div className="evidence__number">L{number}</div>
        <div className="evidence__line" />
      </div>
      <div className="evidence__body">
        <div className="evidence__heading">
          <h3 className="evidence__title">{title}</h3>
          {subtitle && <span className="evidence__subtitle">{subtitle}</span>}
        </div>
        <div className="evidence__content">{children}</div>
      </div>
    </section>
  );
}
