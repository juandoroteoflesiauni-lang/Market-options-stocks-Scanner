import Link from "next/link";

interface NavigationItemProps {
  label: string;
  href: string;
}

export function NavigationItem({ label, href }: NavigationItemProps) {
  return (
    <Link
      href={href}
      className="text-sm text-gray-300 hover:text-white transition-colors duration-200"
    >
      {label}
    </Link>
  );
}
