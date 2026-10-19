export type Category = 'academics' | 'research' | 'awards' | 'international' | 'practice' | 'career' | 'campus' | 'other';
export type Audience = '本科生' | '研究生' | '全体同学' | '未说明';
export interface Activity { id: string; title: string; start: string; end?: string; location: string; precision?: 'minute' | 'day'; sourceExcerpt?: string; yearBasis?: string; }
export interface Deadline { id: string; label: string; at: string; precision: 'minute' | 'day'; sourceExcerpt?: string; yearBasis?: string; }
export interface Article {
  id: string; title: string; summary: string; content: string; category: Category; tags: string[];
  sourceId: string; sourceName?: string; sourceUrl?: string; publishedAt: string; audience: Audience;
  events: Activity[]; deadlines: Deadline[]; actions: string[]; registration: 'open' | 'closed' | 'upcoming' | 'unknown';
  isOpportunity: boolean; materials?: string[]; publisher?: string; sourceCategory?: string;
  processingStatus?: string; processingError?: string; processedAt?: string; classificationMethod?: string;
  demo: boolean; summaryKind: 'sample' | 'excerpt' | 'ai';
  ingestion?: 'crawler' | 'manual';
  contentStatus?: string; fetchedAt?: string; publishedAtInferred?: boolean;
  attachments?: { name: string; url?: string }[];
}
export interface Source { id: string; name: string; short: string; color: string; description: string; }
export interface Filters { q: string; category: string; source: string; audience: string; status: string; period: string; saved: boolean; }
export interface CalendarEntry { id: string; article: Article; title: string; at: string; kind: 'event' | 'deadline'; location?: string; precision: 'minute' | 'day'; }
export interface RemoteList { total: number; loading: boolean; error?: string; hasMore: boolean; loadMore: () => void; }
