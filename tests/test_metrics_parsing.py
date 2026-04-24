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

    def test_parse_cpu_usage_cpuinfo_with_suffix(self) -> None:
        output = "  6.1% 1234/com.example.app:service 4.2% user + 1.9% kernel"
        self.assertEqual(performance_collector.parse_cpu_usage_cpuinfo(output, "com.example.app"), 6.1)

    def test_parse_memory_mb_units(self) -> None:
        self.assertEqual(performance_collector.parse_memory_mb("TOTAL PSS: 1024 KB"), 1.0)
        self.assertEqual(performance_collector.parse_memory_mb("TOTAL PSS: 2 GB"), 2048.0)
        self.assertEqual(performance_collector.parse_memory_mb("TOTAL PSS: 123,904 KB"), 121.0)

    def test_parse_fps_percentile_fallback(self) -> None:
        output = "Graphics info:\n50th percentile: 16ms\n90th percentile: 24ms"
        self.assertEqual(performance_collector.parse_fps(output), 62.5)

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


if __name__ == "__main__":
    unittest.main()
