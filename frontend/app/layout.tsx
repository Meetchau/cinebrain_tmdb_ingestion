import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'CineBrain — your film tonight', description: 'A personal cinematic companion for Meet.' };
export default function RootLayout({children}:Readonly<{children:React.ReactNode}>) { return <html lang="en"><body>{children}</body></html>; }
