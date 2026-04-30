import de from './locales/de.json';
import en from './locales/en.json';

export type LocaleCode = 'en' | 'de';
export type MessageKey = keyof typeof en;

const catalogs: Record<LocaleCode, Record<MessageKey, string>> = {
  en,
  de,
};

export const DEFAULT_LOCALE: LocaleCode = 'en';

export function normalizeLocale(locale?: string): LocaleCode {
  const prefix = (locale || '').toLowerCase().split('-')[0];
  return prefix === 'de' ? 'de' : DEFAULT_LOCALE;
}

export function t(key: MessageKey, locale?: string): string {
  const normalized = normalizeLocale(locale);
  return catalogs[normalized][key] || catalogs[DEFAULT_LOCALE][key] || key;
}
