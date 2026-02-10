import Link from "next/link";

const links = [
  { href: "/", label: "Today" },
  { href: "/news", label: "News" },
  { href: "/drafts", label: "Drafts" },
  { href: "/resolution", label: "Resolution" },
];

export function TopNav() {
  return (
    <nav className="topNav">
      <div className="brand">Lux CRM</div>
      <div className="navLinks">
        {links.map((link) => (
          <Link key={link.href} href={link.href} className="navLink">
            {link.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
