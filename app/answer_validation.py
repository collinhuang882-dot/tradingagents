"""Check computable price/indicator comparisons against cited fields."""
import re


def validate_relations(text, evidence):
    values = {}
    def collect(value):
        if not isinstance(value,dict): return
        for key,item in value.items():
            if isinstance(item,dict): collect(item)
            elif isinstance(item,(int,float)) and not isinstance(item,bool): values.setdefault(key.lower(),[]).append(item)
    for item in evidence: collect(item.get('value'))
    if 'lookback' in values and re.search(r'回看\s*\d+\s*个?交易日', text):
        raise ValueError('公告回看区间按自然日计算，不能表述为交易日')
    name = r'(?:收盘价?|MA\d+|DIF|DEA)'
    pattern = rf'({name})\s*(?:[-+]?\d+(?:\.\d+)?\s*(?:元|点)?)?\s*(高于|低于|大于|小于|>|<|≥|≤)\s*({name})'
    for left,operator,right in re.findall(pattern,text,flags=re.I):
        left = 'close' if left.startswith('收盘') else left.lower()
        right = 'close' if right.startswith('收盘') else right.lower()
        a,b = values.get(left),values.get(right)
        if not a or not b: raise ValueError('比较关系缺少引用字段')
        valid = min(a)>max(b) if operator in ('高于','大于','>') else max(a)<min(b) if operator in ('低于','小于','<') else min(a)>=max(b) if operator=='≥' else max(a)<=min(b)
        if not valid: raise ValueError('引用数值不支持该高低比较关系')


def validate_document_claim(text, evidence):
    for item in evidence:
        document=item.get('document')
        if not document: continue
        if not document.get('body_text') and re.search(r'正文(?:显示|明确|要求|规定)|文件(?:明确|规定|要求)',text):
            raise ValueError('只有标题或摘要，不能声称读取了文件正文')
        if not document.get('published_at') and not re.search(r'时间待核验|时间不确定|发布时间.{0,6}(?:不明|未知|无法)|无法确认|不能确认|未核验|仅.{0,6}标题',text):
            raise ValueError('文件公开时间未核验，结论必须明确时间限制，不能作为历史时点的确定事实')
