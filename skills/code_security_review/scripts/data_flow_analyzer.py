#!/usr/bin/env python3
"""
数据流分析脚本
Phase 3: 深度污点追踪
追踪参数从Controller -> Service -> Mapper -> SQL的完整路径
"""

import os
import re
import json
import argparse
from datetime import datetime

class DataFlowAnalyzer:
    def __init__(self, target_dir, output_dir):
        self.target_dir = os.path.abspath(target_dir)
        self.output_dir = os.path.abspath(output_dir)
        self.results = {
            'data_flows': [],
            'taint_sources': [],
            'taint_sinks': [],
            'vulnerabilities': []
        }
        
        os.makedirs(self.output_dir, exist_ok=True)
    
    def detect_entry_points(self):
        """检测入口点"""
        print("[*] 检测入口点...")
        
        entry_points = []
        
        for root, dirs, files in os.walk(self.target_dir):
            for file in files:
                if not file.endswith(('.php', '.py', '.js', '.java', '.go', '.jsp')):
                    continue
                
                file_path = os.path.join(root, file)
                
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    
                    patterns = {
                        'php': [
                            r'@\$_(GET|POST|REQUEST|SERVER|COOKIE)\[',
                            r'\$_(GET|POST|REQUEST)\[',
                            r'\$\w+\s*=',
                            r'request\.getParameter\(',
                            r'request\.query\[',
                            r'request\.body\['
                        ],
                        'python': [
                            r'@app\.route\s*\(\s*["\']?\s*["\']?\s*\)',
                            r'@blueprint\.route\s*\(\s*["\']?\s*["\']?\s*\)',
                            r'flask\.request\.(args|form|json|data|files)\[',
                            r'request\.(args|form|json|data|files)\['
                        ],
                        'java': [
                            r'@RequestMapping\s*\(\s*["\']?\s*["\']?\s*\)',
                            r'@PostMapping\s*\(\s*["\']?\s*["\']?\s*\)',
                            r'@GetMapping\s*\(\s*["\']?\s*["\']?\s*\)',
                            r'@PutMapping\s*\(\s*["\']?\s*["\']?\s*\)',
                            r'@DeleteMapping\s*\(\s*["\']?\s*["\']?\s*\)',
                            r'HttpServletRequest\.getParameter\(',
                            r'@PathVariable\s*\(\s*["\']?\s*["\']?\s*\)'
                        ],
                        'go': [
                            r'func\s+\w+\s*\(\s*["\']?\s*["\']?\s*\)',
                            r'gin\.Context\.(Query|PostForm|Param)\s*\(\s*["\']?\s*["\']?\s*\)',
                            r'c\.Query\(\s*["\']?\s*["\']?\s*\)',
                            r'c\.PostForm\(\s*["\']?\s*["\']?\s*\)',
                            r'c\.Param\(\s*["\']?\s*["\']?\s*\)'
                        ]
                    }
                    
                    for lang, lang_patterns in patterns.items():
                        for pattern in lang_patterns:
                            matches = re.finditer(pattern, content, re.IGNORECASE)
                            for match in matches:
                                line_number = content[:match.start()].count('\n') + 1
                                line_content = content.split('\n')[line_number - 1].strip()
                                
                                entry_points.append({
                                    'file': file_path,
                                    'language': lang,
                                    'line_number': line_number,
                                    'pattern': pattern,
                                    'line_content': line_content[:100]
                                })
                
                except Exception as e:
                    pass
        
        print(f"[+] 发现 {len(entry_points)} 个入口点")
        return entry_points
    
    def trace_data_flow(self, entry_points):
        """追踪数据流"""
        print("[*] 追踪数据流...")
        
        for entry in entry_points:
            file_path = entry['file']
            language = entry['language']
            
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                if language == 'php':
                    self._trace_php_data_flow(file_path, content)
                elif language == 'python':
                    self._trace_python_data_flow(file_path, content)
                elif language == 'java':
                    self._trace_java_data_flow(file_path, content)
                elif language == 'go':
                    self._trace_go_data_flow(file_path, content)
                    
            except Exception as e:
                print(f"[-] 追踪数据流出错: {str(e)}")
    
    def _trace_php_data_flow(self, file_path, content):
        """追踪PHP数据流"""
        patterns = {
            'sql_sources': [
                r'\$_(GET|POST|REQUEST|COOKIE)\[',
                r'request\.getParameter\(',
                r'request\.query\['
            ],
            'sql_sinks': [
                r'mysql_query\s*\(\s*["\']?\s*["\']?\s*\)',
                r'mysqli_query\s*\(\s*["\']?\s*["\']?\s*\)',
                r'PDO::query\s*\(\s*["\']?\s*["\']?\s*\)',
                r'DB::query\s*\(\s*["\']?\s*["\']?\s*\)',
                r'DB::execute\s*\(\s*["\']?\s*["\']?\s*\)',
                r'pg_query\s*\(\s*["\']?\s*["\']?\s*\)',
                r'pg_execute\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'file_sinks': [
                r'file_get_contents\s*\(\s*["\']?\s*["\']?\s*\)',
                r'file_put_contents\s*\(\s*["\']?\s*["\']?\s*\)',
                r'fopen\s*\(\s*["\']?\s*["\']?\s*\)',
                r'fwrite\s*\(\s*["\']?\s*["\']?\s*\)',
                r'unlink\s*\(\s*["\']?\s*["\']?\s*\)',
                r'include\s*\(\s*["\']?\s*["\']?\s*\)',
                r'require\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'command_sinks': [
                r'system\s*\(\s*["\']?\s*["\']?\s*\)',
                r'exec\s*\(\s*["\']?\s*["\']?\s*\)',
                r'shell_exec\s*\(\s*["\']?\s*["\']?\s*\)',
                r'passthru\s*\(\s*["\']?\s*["\']?\s*\)',
                r'popen\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'output_sinks': [
                r'echo\s+\$\w+',
                r'print\s+\$\w+',
                r'return\s+\$\w+',
                r'header\s*\(\s*["\']?\s*["\']?\s*\)',
                r'file_put_contents\s*\(\s*["\']?\s*["\']?\s*\)'
            ]
        }
        
        for category, sink_patterns in patterns.items():
            for pattern in sink_patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    line_number = content[:match.start()].count('\n') + 1
                    line_content = content.split('\n')[line_number - 1].strip()
                    
                    self.results['data_flows'].append({
                        'file': file_path,
                        'language': 'php',
                        'category': category,
                        'pattern': pattern,
                        'line_number': line_number,
                        'line_content': line_content[:100]
                    })
                    
                    if category == 'sql_sinks':
                        self.results['vulnerabilities'].append({
                            'type': 'sql_injection',
                            'file': file_path,
                            'line_number': line_number,
                            'pattern': pattern,
                            'line_content': line_content[:100],
                            'severity': 'high'
                        })
    
    def _trace_python_data_flow(self, file_path, content):
        """追踪Python数据流"""
        patterns = {
            'sql_sources': [
                r'request\.args\[',
                r'request\.form\[',
                r'request\.json\[',
                r'flask\.request\.(args|form|json|data|files)\['
            ],
            'sql_sinks': [
                r'cursor\.execute\s*\(\s*["\']?\s*["\']?\s*\)',
                r'execute\s*\(\s*["\']?\s*["\']?\s*\)',
                r'executemany\s*\(\s*["\']?\s*["\']?\s*\)',
                r'engine\.execute\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'file_sinks': [
                r'open\s*\(\s*["\']?\s*["\']?\s*\)',
                r'open\s*\(\s*["\']?\s*["\']?\s*\+["\']?w["\']?\s*\+',
                r'Path\s*\(\s*["\']?\s*["\']?\s*\)',
                r'write\s*\(\s*["\']?\s*["\']?\s*\)',
                r'Path\.write_text\s*\(\s*["\']?\s*["\']?\s*\)',
                r'Path\.write_bytes\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'command_sinks': [
                r'os\.system\s*\(\s*["\']?\s*["\']?\s*\)',
                r'subprocess\.(run|call|Popen)\s*\(\s*["\']?\s*["\']?\s*\)',
                r'eval\s*\(\s*["\']?\s*["\']?\s*\)',
                r'exec\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'output_sinks': [
                r'return\s+\$\w+',
                r'print\s*\(\s*["\']?\s*["\']?\s*\)',
                r'render_template\s*\(\s*["\']?\s*["\']?\s*\)',
                r'jsonify\s*\(\s*["\']?\s*["\']?\s*\)'
            ]
        }
        
        for category, sink_patterns in patterns.items():
            for pattern in sink_patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    line_number = content[:match.start()].count('\n') + 1
                    line_content = content.split('\n')[line_number - 1].strip()
                    
                    self.results['data_flows'].append({
                        'file': file_path,
                        'language': 'python',
                        'category': category,
                        'pattern': pattern,
                        'line_number': line_number,
                        'line_content': line_content[:100]
                    })
                    
                    if category == 'sql_sinks':
                        self.results['vulnerabilities'].append({
                            'type': 'sql_injection',
                            'file': file_path,
                            'line_number': line_number,
                            'pattern': pattern,
                            'line_content': line_content[:100],
                            'severity': 'high'
                        })
    
    def _trace_java_data_flow(self, file_path, content):
        """追踪Java数据流"""
        patterns = {
            'sql_sources': [
                r'@RequestParam\s*\(\s*["\']?\s*["\']?\s*\)',
                r'@PathVariable\s*\(\s*["\']?\s*["\']?\s*\)',
                r'@RequestBody\s*\(\s*["\']?\s*["\']?\s*\)',
                r'HttpServletRequest\.getParameter\(',
                r'HttpServletRequest\.getQueryString\('
            ],
            'sql_sinks': [
                r'query\.(execute|executeUpdate|executeUpdate|nativeSQL)\s*\(\s*["\']?\s*["\']?\s*\)',
                r'createNativeQuery\s*\(\s*["\']?\s*["\']?\s*\)',
                r'nativeQuery\s*\(\s*["\']?\s*["\']?\s*\)',
                r'jdbcTemplate\.query\s*\(\s*["\']?\s*["\']?\s*\)',
                r'jdbcTemplate\.update\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'file_sinks': [
                r'File\s*\(\s*["\']?\s*["\']?\s*\)',
                r'Files\.readAllBytes\s*\(\s*["\']?\s*["\']?\s*\)',
                r'FileInputStream\s*\(\s*["\']?\s*["\']?\s*\)',
                r'FileOutputStream\s*\(\s*["\']?\s*["\']?\s*\)',
                r'Paths\.get\s*\(\s*["\']?\s*["\']?\s*\)',
                r'FileWriter\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'command_sinks': [
                r'Runtime\.getRuntime\(\)\.exec\s*\(\s*["\']?\s*["\']?\s*\)',
                r'ProcessBuilder\s*\(\s*["\']?\s*["\']?\s*\)',
                r'ProcessBuilder\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'output_sinks': [
                r'response\.getWriter\(\)\.write\s*\(\s*["\']?\s*["\']?\s*\)',
                r'HttpServletResponse\.getOutputStream\(\)\.write\s*\(\s*["\']?\s*["\']?\s*\)'
            ]
        }
        
        for category, sink_patterns in patterns.items():
            for pattern in sink_patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    line_number = content[:match.start()].count('\n') + 1
                    line_content = content.split('\n')[line_number - 1].strip()
                    
                    self.results['data_flows'].append({
                        'file': file_path,
                        'language': 'java',
                        'category': category,
                        'pattern': pattern,
                        'line_number': line_number,
                        'line_content': line_content[:100]
                    })
                    
                    if category == 'sql_sinks':
                        self.results['vulnerabilities'].append({
                            'type': 'sql_injection',
                            'file': file_path,
                            'line_number': line_number,
                            'pattern': pattern,
                            'line_content': line_content[:100],
                            'severity': 'high'
                        })
    
    def _trace_go_data_flow(self, file_path, content):
        """追踪Go数据流"""
        patterns = {
            'sql_sources': [
                r'c\.Query\(\s*["\']?\s*["\']?\s*\)',
                r'c\.PostForm\(\s*["\']?\s*["\']?\s*\)',
                r'c\.Param\(\s*["\']?\s*["\']?\s*\)',
                r'gin\.Context\.(Query|PostForm|Param)\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'sql_sinks': [
                r'DB\.Query\s*\(\s*["\']?\s*["\']?\s*\)',
                r'DB\.Exec\s*\(\s*["\']?\s*["\']?\s*\)',
                r'db\.Query\s*\(\s*["\']?\s*["\']?\s*\)',
                r'db\.Exec\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'file_sinks': [
                r'os\.Open\s*\(\s*["\']?\s*["\']?\s*\)',
                r'os\.OpenFile\s*\(\s*["\']?\s*["\']?\s*\+["\']?w["\']?\s*\+',
                r'ioutil\.WriteFile\s*\(\s*["\']?\s*["\']?\s*\)',
                r'ioutil\.WriteString\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'command_sinks': [
                r'exec\.Command\s*\(\s*["\']?\s*["\']?\s*\)',
                r'os/exec\.Command\s*\(\s*["\']?\s*["\']?\s*\)',
                r'exec\.CommandContext\s*\(\s*["\']?\s*["\']?\s*\)'
            ],
            'output_sinks': [
                r'c\.JSON\s*\(\s*["\']?\s*["\']?\s*\)',
                r'c\.String\s*\(\s*["\']?\s*["\']?\s*\)',
                r'gin\.Context\.JSON\s*\(\s*["\']?\s*["\']?\s*\)'
            ]
        }
        
        for category, sink_patterns in patterns.items():
            for pattern in sink_patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    line_number = content[:match.start()].count('\n') + 1
                    line_content = content.split('\n')[line_number - 1].strip()
                    
                    self.results['data_flows'].append({
                        'file': file_path,
                        'language': 'go',
                        'category': category,
                        'pattern': pattern,
                        'line_number': line_number,
                        'line_content': line_content[:100]
                    })
                    
                    if category == 'sql_sinks':
                        self.results['vulnerabilities'].append({
                            'type': 'sql_injection',
                            'file': file_path,
                            'line_number': line_number,
                            'pattern': pattern,
                            'line_content': line_content[:100],
                            'severity': 'high'
                        })
    
    def generate_report(self):
        """生成数据流分析报告"""
        print("[*] 生成数据流分析报告...")
        
        report = {
            'scan_date': datetime.now().isoformat(),
            'target_directory': self.target_dir,
            'scan_results': self.results,
            'summary': {
                'total_data_flows': len(self.results['data_flows']),
                'total_vulnerabilities': len(self.results['vulnerabilities']),
                'vulnerability_types': list(set([v['type'] for v in self.results['vulnerabilities']])),
                'languages': list(set([v['language'] for v in self.results['data_flows']]))
            }
        }
        
        output_file = os.path.join(self.output_dir, 'data-flow-analysis-report.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"[+] 数据流分析报告生成完成: {output_file}")
        print(f"    - 总数据流: {report['summary']['total_data_flows']}")
        print(f"    - 漏洞数: {report['summary']['total_vulnerabilities']}")
        print(f"    - 涉及语言: {', '.join(report['summary']['languages'])}")
        print(f"    - 漏洞类型: {', '.join(report['summary']['vulnerability_types'])}")
    
    def run(self):
        """运行数据流分析"""
        print(f"[*] 开始数据流分析: {self.target_dir}")
        print(f"[*] 分析结果将保存至: {self.output_dir}")
        print("=" * 80)
        
        entry_points = self.detect_entry_points()
        self.trace_data_flow(entry_points)
        
        print("=" * 80)
        self.generate_report()
        print("[*] 数据流分析完成！")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="数据流分析工具")
    parser.add_argument('target', help='目标目录')
    parser.add_argument('-o', '--output', default='data-flow-analysis-results', help='输出目录')
    
    args = parser.parse_args()
    
    analyzer = DataFlowAnalyzer(args.target, args.output)
    analyzer.run()