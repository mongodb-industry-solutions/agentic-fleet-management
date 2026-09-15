import "leaflet/dist/leaflet.css";
import "./globals.css";
import { Providers } from "./providers";

export const metadata = {
  title: "Agentic Fleet Management",
  description:
    "Fleet telemetry unified onto COVESA VSS, validated against the spec, and reported on, running on MongoDB Atlas",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
