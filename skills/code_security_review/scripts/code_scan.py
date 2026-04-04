#!/usr/bin/env python3
"""
代码安全扫描主入口
整合所有扫描模块，提供统一的扫描接口
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

# 添加当前目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

# 导入其他扫描模块
try:
    from pattern_scanner import PatternScanner
    from data_flow_analyzer import DataFlowAnalyzer
    from secret_finder import SecretFinder
    from dependency_analyzer import DependencyAnalyzer
except ImportError as e:
    print(f"[!] 导入错误: {e}")
    print("[*] 请确保所有扫描模块在同一目录下")
    sys.exit(1)


class CodeScanner:
    """代码安全扫描器主类"""
    
    def __init__(self, target_path: str, config: Optional[Dict] = None):
        self.target_path = Path(target_path)
        self.config = config or {}
        self.results = {
            'target': str(self.target_path),
            'scan_time': None,
            'findings': [],
            'summary': {}
        }
        
    def scan(self, scan_types: List[str] = None) -> Dict:
        """
        执行代码安全扫描
        
        Args:
            scan_types: 扫描类型列表，可选值：
                       - pattern: 模式匹配扫描
                       - dataflow: 数据流分析
                       - secret: 敏感信息检测
                       - dependency: 依赖分析
                       - all: 全部扫描
        
        Returns:
            扫描结果字典
        """
        if scan_types is None or 'all' in scan_types:
            scan_types = ['pattern', 'dataflow', 'secret', 'dependency']
            
        print(f"[*] 开始扫描: {self.target_path}")
        print(f"[*] 扫描类型: {', '.join(scan_types)}")
        
        # 执行各类扫描
        if 'pattern' in scan_types:
            self._run_pattern_scan()
            
        if 'dataflow' in scan_types:
            self._run_dataflow_scan()
            
        if 'secret' in scan_types:
            self._run_secret_scan()
            
        if 'dependency' in scan_types:
            self._run_dependency_scan()
            
        # 生成摘要
        self._generate_summary()
        
        return self.results
        
    def _run_pattern_scan(self):
        """运行模式匹配扫描"""
        print("\n[*] 执行模式匹配扫描...")
        scanner = PatternScanner(str(self.target_path))
        findings = scanner.scan()
        
        for finding in findings:
            self.results['findings'].append({
                'type': 'pattern',
                'severity': finding.get('severity', 'medium'),
                'category': finding.get('category', 'unknown'),
                'file': finding.get('file', ''),
                'line': finding.get('line', 0),
                'message': finding.get('message', ''),
                'code': finding.get('code', '')
            })
            
        print(f"[+] 模式匹配扫描完成，发现 {len(findings)} 个问题")
        
    def _run_dataflow_scan(self):
        """运行数据流分析"""
        print("\n[*] 执行数据流分析...")
        analyzer = DataFlowAnalyzer(str(self.target_path))
        findings = analyzer.analyze()
        
        for finding in findings:
            self.results['findings'].append({
                'type': 'dataflow',
                'severity': finding.get('severity', 'high'),
                'category': finding.get('vulnerability_type', 'unknown'),
                'file': finding.get('file', ''),
                'source': finding.get('source', {}),
                'sink': finding.get('sink', {}),
                'dataflow_path': finding.get('dataflow_path', []),
                'message': finding.get('message', '')
            })
            
        print(f"[+] 数据流分析完成，发现 {len(findings)} 个问题")
        
    def _run_secret_scan(self):
        """运行敏感信息检测"""
        print("\n[*] 执行敏感信息检测...")
        finder = SecretFinder(str(self.target_path))
        findings = finder.find()
        
        for finding in findings:
            self.results['findings'].append({
                'type': 'secret',
                'severity': 'high',
                'category': finding.get('type', 'unknown'),
                'file': finding.get('file', ''),
                'line': finding.get('line', 0),
                'message': f"发现 {finding.get('type', 'unknown')}: {finding.get('value', '')[:20]}..."
            })
            
        print(f"[+] 敏感信息检测完成，发现 {len(findings)} 个问题")
        
    def _run_dependency_scan(self):
        """运行依赖分析"""
        print("\n[*] 执行依赖分析...")
        analyzer = DependencyAnalyzer(str(self.target_path))
        findings = analyzer.analyze()
        
        for finding in findings:
            self.results['findings'].append({
                'type': 'dependency',
                'severity': finding.get('severity', 'medium'),
                'category': 'vulnerable_dependency',
                'package': finding.get('package', ''),
                'version': finding.get('version', ''),
                'cve': finding.get('cve', ''),
                'message': finding.get('message', '')
            })
            
        print(f"[+] 依赖分析完成，发现 {len(findings)} 个问题")
        
    def _generate_summary(self):
        """生成扫描摘要"""
        findings = self.results['findings']
        
        severity_count = {
            'critical': 0,
            'high': 0,
            'medium': 0,
            'low': 0,
            'info': 0
        }
        
        type_count = {}
        
        for finding in findings:
            severity = finding.get('severity', 'medium').lower()
            severity_count[severity] = severity_count.get(severity, 0) + 1
            
            finding_type = finding.get('type', 'unknown')
            type_count[finding_type] = type_count.get(finding_type, 0) + 1
            
        self.results['summary'] = {
            'total_findings': len(findings),
            'severity_distribution': severity_count,
            'type_distribution': type_count
        }
        
    def export_json(self, output_path: str):
        """导出结果为 JSON 格式"""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        print(f"\n[+] 结果已导出: {output_path}")
        
    def export_markdown(self, output_path: str):
        """导出结果为 Markdown 格式"""
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("# 代码安全扫描报告\n\n")
            f.write(f"**扫描目标**: {self.results['target']}\n\n")
            f.write(f"**发现问题总数**: {self.results['summary']['total_findings']}\n\n")
            
            # 严重程度分布
            f.write("## 严重程度分布\n\n")
            for severity, count in self.results['summary']['severity_distribution'].items():
                if count > 0:
                    f.write(f"- **{severity.upper()}**: {count}\n")
            f.write("\n")
            
            # 详细发现
            f.write("## 详细发现\n\n")
            for i, finding in enumerate(self.results['findings'], 1):
                f.write(f"### 问题 {i}\n\n")
                f.write(f"- **类型**: {finding.get('type', 'unknown')}\n")
                f.write(f"- **严重程度**: {finding.get('severity', 'medium')}\n")
                f.write(f"- **类别**: {finding.get('category', 'unknown')}\n")
                
                if 'file' in finding:
                    f.write(f"- **文件**: {finding['file']}\n")
                if 'line' in finding:
                    f.write(f"- **行号**: {finding['line']}\n")
                    
                f.write(f"- **描述**: {finding.get('message', '')}\n\n")
                
        print(f"[+] 报告已导出: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='代码安全扫描工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s /path/to/project
  %(prog)s /path/to/project --type pattern,secret
  %(prog)s /path/to/project --output report.json --format json
        """
    )
    
    parser.add_argument('target', help='扫描目标路径')
    parser.add_argument(
        '--type', '-t',
        default='all',
        help='扫描类型: all, pattern, dataflow, secret, dependency (逗号分隔)'
    )
    parser.add_argument(
        '--output', '-o',
        default='scan_report',
        help='输出文件路径 (不含扩展名)'
    )
    parser.add_argument(
        '--format', '-f',
        choices=['json', 'markdown', 'both'],
        default='both',
        help='输出格式'
    )
    
    args = parser.parse_args()
    
    # 解析扫描类型
    scan_types = [t.strip() for t in args.type.split(',')]
    
    # 创建扫描器并执行扫描
    scanner = CodeScanner(args.target)
    results = scanner.scan(scan_types)
    
    # 导出结果
    if args.format in ['json', 'both']:
        scanner.export_json(f"{args.output}.json")
        
    if args.format in ['markdown', 'both']:
        scanner.export_markdown(f"{args.output}.md")
        
    # 打印摘要
    print("\n" + "="*50)
    print("扫描摘要")
    print("="*50)
    print(f"总发现问题: {results['summary']['total_findings']}")
    print("\n严重程度分布:")
    for severity, count in results['summary']['severity_distribution'].items():
        if count > 0:
            print(f"  {severity.upper()}: {count}")
            
    return 0 if results['summary']['total_findings'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
