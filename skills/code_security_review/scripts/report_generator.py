#!/usr/bin/env python3
"""
报告生成脚本
Phase 5: 结构化报告
生成标准化安全审计报告和修复建议
"""

import os
import json
import argparse
from datetime import datetime

class ReportGenerator:
    def __init__(self, input_dir, output_dir):
        self.input_dir = os.path.abspath(input_dir)
        self.output_dir = os.path.abspath(output_dir)
        self.scan_results = {}
        
        os.makedirs(self.output_dir, exist_ok=True)
    
    def load_scan_results(self):
        """加载扫描结果"""
        print("[*] 加载扫描结果...")
        
        result_files = {
            'dependency_analysis': 'dependency-analysis-report.json',
            'pattern_scan': 'pattern-scan-report.json',
            'secret_scan': 'secret-scan-report.json',
            'data_flow_analysis': 'data-flow-analysis-report.json',
            'config_scan': 'config-scan-report.json',
            'admin_panel': 'admin-panel-report.json'
        }
        
        for scan_type, filename in result_files.items():
            file_path = os.path.join(self.input_dir, filename)
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        self.scan_results[scan_type] = json.load(f)
                    print(f"[+] 加载 {scan_type} 结果")
                except Exception as e:
                    print(f"[-] 加载 {scan_type} 结果出错: {str(e)}")
        
        print(f"[+] 加载完成: {len(self.scan_results)} 个扫描结果")
    
    def analyze_vulnerabilities(self):
        """分析漏洞"""
        print("[*] 分析漏洞...")
        
        vulnerabilities = {
            'critical': [],
            'high': [],
            'medium': [],
            'low': []
        }
        
        for scan_type, results in self.scan_results.items():
            if 'scan_results' in results:
                scan_results = results['scan_results']
                
                if 'vulnerabilities' in scan_results:
                    for vuln in scan_results['vulnerabilities']:
                        severity = vuln.get('severity', 'medium').lower()
                        if severity in vulnerabilities:
                            vulnerabilities[severity].append({
                                **vuln,
                                'source': scan_type
                            })
                
                for category, items in scan_results.items():
                    if isinstance(items, list) and items:
                        for item in items:
                            if 'severity' in item:
                                severity = item['severity'].lower()
                                if severity in vulnerabilities:
                                    vulnerabilities[severity].append({
                                        **item,
                                        'source': scan_type
                                    })
        
        print(f"[+] 分析完成:")
        print(f"    - Critical: {len(vulnerabilities['critical'])}")
        print(f"    - High: {len(vulnerabilities['high'])}")
        print(f"    - Medium: {len(vulnerabilities['medium'])}")
        print(f"    - Low: {len(vulnerabilities['low'])}")
        
        return vulnerabilities
    
    def generate_html_report(self, vulnerabilities):
        """生成HTML报告"""
        print("[*] 生成HTML报告...")
        
        html_template = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>代码安全审计报告</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }
        .header h1 {
            margin: 0;
            font-size: 28px;
        }
        .header p {
            margin: 10px 0 0 0;
            opacity: 0.9;
        }
        .summary {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .summary-card {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }
        .summary-card h3 {
            margin: 0 0 10px 0;
            color: #666;
            font-size: 14px;
            text-transform: uppercase;
        }
        .summary-card .count {
            font-size: 36px;
            font-weight: bold;
            color: #667eea;
        }
        .vulnerability-section {
            margin-bottom: 30px;
        }
        .vulnerability-section h2 {
            color: #667eea;
            border-bottom: 2px solid #667eea;
            padding-bottom: 10px;
            margin-bottom: 20px;
        }
        .vulnerability-card {
            background: white;
            border-left: 4px solid #667eea;
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 4px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }
        .vulnerability-card.critical {
            border-left-color: #dc3545;
        }
        .vulnerability-card.high {
            border-left-color: #fd7e14;
        }
        .vulnerability-card.medium {
            border-left-color: #ffc107;
        }
        .vulnerability-card.low {
            border-left-color: #28a745;
        }
        .vulnerability-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .vulnerability-title {
            font-size: 18px;
            font-weight: bold;
        }
        .vulnerability-severity {
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
            text-transform: uppercase;
        }
        .severity-critical {
            background: #dc3545;
            color: white;
        }
        .severity-high {
            background: #fd7e14;
            color: white;
        }
        .severity-medium {
            background: #ffc107;
            color: #333;
        }
        .severity-low {
            background: #28a745;
            color: white;
        }
        .vulnerability-details {
            margin-top: 15px;
        }
        .vulnerability-detail {
            margin-bottom: 10px;
        }
        .vulnerability-detail strong {
            color: #667eea;
        }
        .code-block {
            background: #f4f4f4;
            padding: 15px;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
            font-size: 14px;
            overflow-x: auto;
            margin-top: 10px;
        }
        .recommendation {
            background: #e7f3ff;
            padding: 15px;
            border-radius: 4px;
            margin-top: 15px;
            border-left: 4px solid #007bff;
        }
        .recommendation h4 {
            margin: 0 0 10px 0;
            color: #007bff;
        }
        .footer {
            text-align: center;
            margin-top: 50px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
            color: #666;
        }
        .toc {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 30px;
        }
        .toc h2 {
            margin: 0 0 15px 0;
            color: #667eea;
        }
        .toc ul {
            list-style: none;
            padding: 0;
            margin: 0;
        }
        .toc li {
            padding: 8px 0;
            border-bottom: 1px solid #e9ecef;
        }
        .toc a {
            color: #667eea;
            text-decoration: none;
        }
        .toc a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>代码安全审计报告</h1>
        <p>生成时间: {report_date}</p>
        <p>目标目录: {target_directory}</p>
    </div>
    
    <div class="summary">
        <div class="summary-card">
            <h3>严重漏洞</h3>
            <div class="count">{critical_count}</div>
        </div>
        <div class="summary-card">
            <h3>高危漏洞</h3>
            <div class="count">{high_count}</div>
        </div>
        <div class="summary-card">
            <h3>中危漏洞</h3>
            <div class="count">{medium_count}</div>
        </div>
        <div class="summary-card">
            <h3>低危漏洞</h3>
            <div class="count">{low_count}</div>
        </div>
    </div>
    
    <div class="toc">
        <h2>目录</h2>
        <ul>
            <li><a href="#critical">严重漏洞</a></li>
            <li><a href="#high">高危漏洞</a></li>
            <li><a href="#medium">中危漏洞</a></li>
            <li><a href="#low">低危漏洞</a></li>
        </ul>
    </div>
    
    <div class="vulnerability-section" id="critical">
        <h2>严重漏洞</h2>
        {critical_vulnerabilities}
    </div>
    
    <div class="vulnerability-section" id="high">
        <h2>高危漏洞</h2>
        {high_vulnerabilities}
    </div>
    
    <div class="vulnerability-section" id="medium">
        <h2>中危漏洞</h2>
        {medium_vulnerabilities}
    </div>
    
    <div class="vulnerability-section" id="low">
        <h2>低危漏洞</h2>
        {low_vulnerabilities}
    </div>
    
    <div class="footer">
        <p>本报告由代码安全审计专家工具自动生成</p>
        <p>生成时间: {report_date}</p>
    </div>
</body>
</html>
        """
        
        critical_html = self._generate_vulnerability_html(vulnerabilities['critical'], 'critical')
        high_html = self._generate_vulnerability_html(vulnerabilities['high'], 'high')
        medium_html = self._generate_vulnerability_html(vulnerabilities['medium'], 'medium')
        low_html = self._generate_vulnerability_html(vulnerabilities['low'], 'low')
        
        html_content = html_template.format(
            report_date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            target_directory=self.input_dir,
            critical_count=len(vulnerabilities['critical']),
            high_count=len(vulnerabilities['high']),
            medium_count=len(vulnerabilities['medium']),
            low_count=len(vulnerabilities['low']),
            critical_vulnerabilities=critical_html,
            high_vulnerabilities=high_html,
            medium_vulnerabilities=medium_html,
            low_vulnerabilities=low_html
        )
        
        output_file = os.path.join(self.output_dir, 'security-audit-report.html')
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"[+] HTML报告生成完成: {output_file}")
    
    def _generate_vulnerability_html(self, vulnerabilities, severity):
        """生成漏洞HTML"""
        if not vulnerabilities:
            return '<p>未发现漏洞</p>'
        
        html = ''
        for vuln in vulnerabilities:
            vuln_type = vuln.get('type', 'unknown')
            file_path = vuln.get('file', 'N/A')
            line_number = vuln.get('line_number', 'N/A')
            line_content = vuln.get('line_content', 'N/A')
            source = vuln.get('source', 'unknown')
            
            html += f'''
            <div class="vulnerability-card {severity}">
                <div class="vulnerability-header">
                    <div class="vulnerability-title">{vuln_type}</div>
                    <div class="vulnerability-severity severity-{severity}">{severity}</div>
                </div>
                <div class="vulnerability-details">
                    <div class="vulnerability-detail">
                        <strong>文件:</strong> {file_path}:{line_number}
                    </div>
                    <div class="vulnerability-detail">
                        <strong>来源:</strong> {source}
                    </div>
                    <div class="vulnerability-detail">
                        <strong>代码:</strong>
                        <div class="code-block">{line_content}</div>
                    </div>
                </div>
                <div class="recommendation">
                    <h4>修复建议</h4>
                    <p>请参考SKILL.md中对应的漏洞类型章节获取详细的修复建议。</p>
                </div>
            </div>
            '''
        
        return html
    
    def generate_json_report(self, vulnerabilities):
        """生成JSON报告"""
        print("[*] 生成JSON报告...")
        
        report = {
            'report_date': datetime.now().isoformat(),
            'target_directory': self.input_dir,
            'scan_results': self.scan_results,
            'vulnerabilities': vulnerabilities,
            'summary': {
                'critical': len(vulnerabilities['critical']),
                'high': len(vulnerabilities['high']),
                'medium': len(vulnerabilities['medium']),
                'low': len(vulnerabilities['low']),
                'total': sum([len(v) for v in vulnerabilities.values()])
            }
        }
        
        output_file = os.path.join(self.output_dir, 'security-audit-report.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"[+] JSON报告生成完成: {output_file}")
    
    def generate_markdown_report(self, vulnerabilities):
        """生成Markdown报告"""
        print("[*] 生成Markdown报告...")
        
        md_content = f"""# 代码安全审计报告

**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**目标目录**: {self.input_dir}

## 执行摘要

| 严重程度 | 数量 |
|---------|------|
| 严重 | {len(vulnerabilities['critical'])} |
| 高危 | {len(vulnerabilities['high'])} |
| 中危 | {len(vulnerabilities['medium'])} |
| 低危 | {len(vulnerabilities['low'])} |
| **总计** | **{sum([len(v) for v in vulnerabilities.values()])}** |

## 严重漏洞

"""
        
        md_content += self._generate_vulnerability_markdown(vulnerabilities['critical'], '严重')
        
        md_content += """
## 高危漏洞

"""
        md_content += self._generate_vulnerability_markdown(vulnerabilities['high'], '高危')
        
        md_content += """
## 中危漏洞

"""
        md_content += self._generate_vulnerability_markdown(vulnerabilities['medium'], '中危')
        
        md_content += """
## 低危漏洞

"""
        md_content += self._generate_vulnerability_markdown(vulnerabilities['low'], '低危')
        
        md_content += """
## 修复建议

请参考SKILL.md中对应的漏洞类型章节获取详细的修复建议。

## 附录

本报告由代码安全审计专家工具自动生成。
"""
        
        output_file = os.path.join(self.output_dir, 'security-audit-report.md')
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"[+] Markdown报告生成完成: {output_file}")
    
    def _generate_vulnerability_markdown(self, vulnerabilities, severity):
        """生成漏洞Markdown"""
        if not vulnerabilities:
            return '未发现漏洞\n\n'
        
        md = ''
        for vuln in vulnerabilities:
            vuln_type = vuln.get('type', 'unknown')
            file_path = vuln.get('file', 'N/A')
            line_number = vuln.get('line_number', 'N/A')
            line_content = vuln.get('line_content', 'N/A')
            source = vuln.get('source', 'unknown')
            
            md += f"""
### {vuln_type}

**严重程度**: {severity}
**文件**: [{file_path}:{line_number}]({file_path}:{line_number})
**来源**: {source}

**代码**:
```
{line_content}
```

**修复建议**:
请参考SKILL.md中对应的漏洞类型章节获取详细的修复建议。

---

"""
        
        return md
    
    def run(self):
        """运行报告生成"""
        print(f"[*] 开始生成报告: {self.input_dir}")
        print(f"[*] 报告将保存至: {self.output_dir}")
        print("=" * 80)
        
        self.load_scan_results()
        vulnerabilities = self.analyze_vulnerabilities()
        
        print("=" * 80)
        self.generate_json_report(vulnerabilities)
        self.generate_html_report(vulnerabilities)
        self.generate_markdown_report(vulnerabilities)
        
        print("[*] 报告生成完成！")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="报告生成工具")
    parser.add_argument('input', help='输入目录（包含扫描结果）')
    parser.add_argument('-o', '--output', default='security-audit-report', help='输出目录')
    
    args = parser.parse_args()
    
    generator = ReportGenerator(args.input, args.output)
    generator.run()