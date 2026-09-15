"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import Badge from "@leafygreen-ui/badge";
import Button from "@leafygreen-ui/button";
import Icon from "@leafygreen-ui/icon";
import { palette } from "@leafygreen-ui/palette";

import BehindTheScenes from "@/components/BehindTheScenes";
import InfoWizard from "@/components/infoWizard/InfoWizard";
import { TALK_TRACK } from "@/lib/const/talkTrack";
import { useBehindTheScenes } from "@/lib/context/BehindTheScenesContext";

// Ordered to match the four steps on the overview: catch bad data, fuse the
// feeds, search parts and damage, then act. Recall sits next to Agent because
// the vehicles it cannot locate are what the agent goes after.
const NAV = [
  { href: "/", label: "Overview" },
  { href: "/data-quality", label: "Data quality" },
  { href: "/fleet", label: "Fleet" },
  { href: "/claims", label: "Damage" },
  { href: "/recall", label: "Recall" },
  { href: "/agent", label: "Agent" },
];

/**
 * Product chrome. The operator sees a fleet management application; the reveal
 * is one button away rather than occupying a third of the screen.
 */
export default function AppShell({ children, health }) {
  const pathname = usePathname();
  const { setOpen, panels } = useBehindTheScenes();

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b sticky top-0 z-30">
        <div className="max-w-[1500px] mx-auto px-6 h-14 flex items-center gap-6">
          <Link href="/" className="flex items-center gap-2 no-underline">
            <Image
              src="/mongodb-leaf.png"
              alt="MongoDB"
              width={11}
              height={22}
              priority
              className="shrink-0"
            />
            <span
              className="font-semibold tracking-tight"
              style={{ color: palette.green.dark2 }}
            >
              Leafy Fleet Ops
            </span>
          </Link>

          <nav className="flex items-center gap-1">
            {NAV.map((item) => {
              const active =
                item.href === "/"
                  ? pathname === "/"
                  : item.href === "/fleet"
                    ? pathname.startsWith("/fleet") || pathname.startsWith("/vehicle")
                    : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className="px-3 py-1.5 rounded text-sm no-underline transition-colors"
                  style={{
                    color: active ? palette.green.dark2 : palette.gray.dark1,
                    backgroundColor: active ? palette.green.light3 : "transparent",
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="flex-1" />

          {health && (
            <Badge variant="lightgray">VSS {health.vssVersion}</Badge>
          )}

          <Button
            size="small"
            variant="primaryOutline"
            leftGlyph={<Icon glyph="Wizard" />}
            onClick={() => setOpen(true)}
            disabled={panels.length === 0}
          >
            Behind the scenes
          </Button>

          <InfoWizard iconGlyph="InfoWithCircle" sections={TALK_TRACK} />
        </div>
      </header>

      <main className="max-w-[1500px] mx-auto px-6 py-5">{children}</main>

      <BehindTheScenes />
    </div>
  );
}
