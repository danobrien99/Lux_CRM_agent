"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const links = [
  { href: "/", label: "Priority Contacts" },
  { href: "/news", label: "News" },
  { href: "/drafts", label: "Drafts" },
  { href: "/resolution", label: "Resolution" },
];

export function TopNav() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => {
      setScrolled(window.scrollY > 20);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  return (
    <nav className={`topNav${scrolled ? " topNavScrolled" : ""}`}>
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
