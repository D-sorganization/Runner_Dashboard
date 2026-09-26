/**
 * Web Vitals reporter (issues #385, #1550).
 *
 * Routes Web Vitals performance metrics to `/api/metrics/web-vitals` through
 * `apiRequest` so requests carry the `X-Requested-With: XMLHttpRequest` CSRF
 * sentinel header required by backend middleware (`backend/middleware.py::csrf_check`).
 */

import { onCLS, onINP, onFCP, onLCP } from 'web-vitals'
import { apiRequest } from './api'

export interface WebVitalMetric {
  name: string
  value: number
  rating?: string
  delta?: number
  id?: string
  navigationType?: string
}

export interface WebVitalsPayload {
  route: string
  metrics: Array<{
    name: string
    value: number
    rating: string
    delta: number | null
    id: string
    navigation_type: string
  }>
}

export function buildWebVitalsPayload(
  metric: WebVitalMetric,
  pathname: string = typeof window !== 'undefined' ? window.location.pathname : '',
): WebVitalsPayload {
  return {
    route: pathname,
    metrics: [{
      name: metric.name,
      value: metric.value,
      rating: metric.rating || '',
      delta: metric.delta ?? null,
      id: metric.id || '',
      navigation_type: metric.navigationType || '',
    }],
  }
}

export function sendWebVitals(metric: WebVitalMetric): Promise<unknown> {
  const payload = buildWebVitalsPayload(metric)
  return apiRequest('/api/metrics/web-vitals', {
    body: payload,
  }).catch(() => {
    // Non-blocking telemetry — silently ignore reporting failures
  })
}

export function initWebVitals(): void {
  onCLS(sendWebVitals)
  onINP(sendWebVitals)
  onFCP(sendWebVitals)
  onLCP(sendWebVitals)
}
