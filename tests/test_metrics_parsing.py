import unittest

import performance_collector
import report_generator


class PerformanceCollectorParsingTests(unittest.TestCase):
    def test_parse_cpu_usage_with_percent(self) -> None:
        output = "1234 12% S com.example.app"
        self.assertEqual(performance_collector.parse_cpu_usage(output, "com.example.app"), 12.0)

    def test_parse_cpu_usage_column_style(self) -> None:
        output = "1234 u0_a123 10 -10 5G 200M 120M S 24.5 3.1 00:03.42 com.example.app"
        self.assertEqual(performance_collector.parse_cpu_usage(output, "com.example.app"), 24.5)

    def test_parse_cpu_usage_numeric_prefix(self) -> None:
        output = "1234 18 com.example.app"
        self.assertEqual(performance_collector.parse_cpu_usage(output, "com.example.app"), 18.0)

    def test_parse_cpu_usage_cpuinfo_with_suffix(self) -> None:
        output = "  6.1% 1234/com.example.app:service 4.2% user + 1.9% kernel"
        self.assertEqual(performance_collector.parse_cpu_usage_cpuinfo(output, "com.example.app"), 6.1)

    def test_parse_memory_mb_units(self) -> None:
        self.assertEqual(performance_collector.parse_memory_mb("TOTAL PSS: 1024 KB"), 1.0)
        self.assertEqual(performance_collector.parse_memory_mb("TOTAL PSS: 2 GB"), 2048.0)
        self.assertEqual(performance_collector.parse_memory_mb("TOTAL PSS: 123,904 KB"), 121.0)

    def test_parse_memory_metrics_extracts_pss_and_rss(self) -> None:
        output = "App Summary\nTOTAL PSS: 2048 KB\nTOTAL RSS: 4096 KB"
        metrics = performance_collector.parse_memory_metrics(output)
        self.assertEqual(metrics["total_pss_mb"], 2.0)
        self.assertEqual(metrics["total_rss_mb"], 4.0)
        self.assertEqual(metrics["total_mb"], 2.0)

    def test_parse_fps_percentile_fallback(self) -> None:
        output = "Graphics info:\n50th percentile: 16ms\n90th percentile: 24ms"
        self.assertEqual(performance_collector.parse_fps(output), 62.5)

    def test_parse_surfaceflinger_fps(self) -> None:
        output = "16666666\n0 1000000000 0\n0 1016666666 0\n0 1033333332 0\n"
        self.assertEqual(performance_collector.parse_surfaceflinger_fps(output), 60.0)

    def test_parse_launch_times_partial(self) -> None:
        result = performance_collector.parse_launch_times("ThisTime: 11\nTotalTime: 22")
        self.assertEqual(result["ThisTime"], 11)
        self.assertEqual(result["TotalTime"], 22)
        self.assertEqual(result["WaitTime"], "N/A")


class ReportGeneratorPayloadTests(unittest.TestCase):
    def test_chart_payload_uses_nullable_padding(self) -> None:
        rows = [
            {
                "device": "d1",
                "cold_avg": 1200.0,
                "warm_avg": 900.0,
                "hot_avg": 700.0,
                "cold_values": [1000.0, 1100.0],
                "warm_values": [900.0],
                "hot_values": [],
            }
        ]
        payload = report_generator._chart_payload(rows)
        self.assertEqual(payload["startup_metrics"]["labels"], [1, 2])
        self.assertEqual(payload["startup_metrics"]["cold"]["values"], [1000.0, 1100.0])
        self.assertEqual(payload["startup_metrics"]["warm"]["values"], [900.0, None])
        self.assertEqual(payload["startup_metrics"]["hot"]["values"], [None, None])

    def test_collect_rows_supports_legacy_warm_start_keys(self) -> None:
        devices = {
            "device-1": {
                "startup_metrics": {
                    "cold_start": {"average": "1200", "samples": ["1100", "1300"]},
                    "warm_start": {"average": "850", "samples": ["800", "900"]},
                    "hot_start": {"average": "700", "samples": ["690"]},
                }
            }
        }
        _, startup_rows, _ = report_generator._collect_rows(devices)
        self.assertEqual(startup_rows[0]["cold_avg"], 1200.0)
        self.assertEqual(startup_rows[0]["warm_avg"], 850.0)
        self.assertEqual(startup_rows[0]["hot_avg"], 700.0)
        self.assertEqual(startup_rows[0]["warm_values"], [800.0, 900.0])


if __name__ == "__main__":
    unittest.main()
