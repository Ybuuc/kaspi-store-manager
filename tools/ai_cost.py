"""Measured proxy token counts plus clearly hypothetical API/GPU operating scenarios."""
import json
from pathlib import Path


def main():
    import tiktoken
    root=Path(__file__).resolve().parents[1]/'docs/course/ai'
    cases=json.loads((root/'eval.json').read_text(encoding='utf-8'))
    encoder=tiktoken.get_encoding('cl100k_base')
    inputs=[len(encoder.encode(c['question'])) for c in cases]
    outputs=[len(encoder.encode(c['reference'])) for c in cases]
    def cost(requests,input_tokens,output_tokens):
        return requests*(input_tokens*.30+output_tokens*2.50)/1_000_000
    measured_input=sum(inputs)/len(inputs)
    measured_output=sum(outputs)/len(outputs)
    report=dict(tokenizer='cl100k_base; proxy only, NOT Gemini native billing tokenizer',
                measured_eval_mean_input=measured_input,measured_reference_mean_output=measured_output,
                excludes='system prompt, retrieval, thinking tokens and actual generated output',
                official_api_rates_usd_per_million=dict(input=.30,output=2.50,model='gemini-3.5-flash-lite',
                    source='https://aistudio.google.com/docs/pricing#gemini-3.5-flash-lite',checked='2026-11-19'),
                measured_proxy_10000_requests_usd=cost(10000,measured_input,measured_output),
                planning_assumptions=dict(requests_month=10000,input_tokens=800,output_tokens=200,gpu_usd_month=300,
                                          gpu_rate_status='assumption, not official quote'),
                planning_usd=cost(10000,800,200),planning_10x_usd=cost(100000,800,200),
                gpu_break_even_requests=300/cost(1,800,200))
    (root/'cost-results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
