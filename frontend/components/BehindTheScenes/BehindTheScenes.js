"use client";

import Badge from "@leafygreen-ui/badge";
import Icon from "@leafygreen-ui/icon";
import IconButton from "@leafygreen-ui/icon-button";
import { palette } from "@leafygreen-ui/palette";
import { Body, Overline, Subtitle } from "@leafygreen-ui/typography";

import { useBehindTheScenes } from "@/lib/context/BehindTheScenesContext";

/**
 * The reveal. Slides over the product to show what MongoDB is doing for the
 * screen the operator is currently on.
 *
 * Panels come from the API response rather than being hardcoded here, so the
 * explanation always matches the data that was actually returned.
 */
export default function BehindTheScenes() {
  const { open, setOpen, panels, context } = useBehindTheScenes();

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 bg-black/20 z-40"
          onClick={() => setOpen(false)}
          aria-hidden
        />
      )}

      <aside
        className="fixed top-0 right-0 h-full z-50 shadow-2xl flex flex-col transition-transform duration-300 ease-out"
        style={{
          width: "min(560px, 92vw)",
          backgroundColor: palette.black,
          color: palette.gray.light2,
          transform: open ? "translateX(0)" : "translateX(100%)",
        }}
        aria-hidden={!open}
      >
        <header
          className="flex items-start justify-between gap-4 px-5 py-4 border-b shrink-0"
          style={{ borderColor: palette.gray.dark2 }}
        >
          <div>
            <Overline style={{ color: palette.green.base }}>Behind the scenes</Overline>
            <div className="text-lg font-semibold" style={{ color: palette.white }}>
              {context || "How MongoDB does this"}
            </div>
          </div>
          <IconButton
            aria-label="Close"
            onClick={() => setOpen(false)}
            darkMode
          >
            <Icon glyph="X" />
          </IconButton>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-5">
          {panels.length === 0 && (
            <Body style={{ color: palette.gray.light1 }}>
              Nothing to show for this view yet.
            </Body>
          )}

          {panels.map((panel, index) => (
            <section key={index} className="flex flex-col gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                <Subtitle style={{ color: palette.white }}>{panel.title}</Subtitle>
              </div>

              <div className="flex gap-2 flex-wrap">
                <Badge variant="green">{panel.capability}</Badge>
                {panel.collection && (
                  <Badge variant="darkgray">{panel.collection}</Badge>
                )}
                {panel.tripCount !== undefined && (
                  <Badge variant="blue">{panel.tripCount} trips derived</Badge>
                )}
                {panel.findingCount !== undefined && (
                  <Badge variant="yellow">{panel.findingCount} findings</Badge>
                )}
                {panel.documentBytes !== undefined && (
                  <Badge variant="lightgray">
                    {panel.documentBytes.toLocaleString("en-US")} B document
                  </Badge>
                )}
              </div>

              {panel.code && (
                <pre
                  className="text-xs leading-relaxed overflow-x-auto rounded p-3 m-0"
                  style={{
                    backgroundColor: palette.gray.dark3,
                    color: palette.green.light2,
                    border: `1px solid ${palette.gray.dark2}`,
                  }}
                >
                  <code>{panel.code}</code>
                </pre>
              )}

              <Body style={{ color: palette.gray.light1 }} className="text-sm">
                {panel.note}
              </Body>
            </section>
          ))}
        </div>

        <footer
          className="px-5 py-3 border-t shrink-0 text-xs"
          style={{ borderColor: palette.gray.dark2, color: palette.gray.base }}
        >
          Signals follow COVESA VSS 6.1.0. Validation rules, units and ranges are
          generated from the specification rather than written by hand.
        </footer>
      </aside>
    </>
  );
}
