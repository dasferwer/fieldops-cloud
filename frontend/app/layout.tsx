import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';

import './globals.css';
import { Providers } from './providers';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin', 'cyrillic'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin', 'cyrillic'],
});

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.FIELDOPS_SITE_URL ?? 'http://localhost:8060',
  ),
  title: 'FieldOps Cloud — управление выездными работами',
  description:
    'Операционная платформа для управления заявками, специалистами, SLA и выездными работами.',
  openGraph: {
    title: 'FieldOps Cloud',
    description: 'Управление выездными работами, SLA и загрузкой команды.',
    images: ['/og.png'],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'FieldOps Cloud',
    description: 'Управление выездными работами, SLA и загрузкой команды.',
    images: ['/og.png'],
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
