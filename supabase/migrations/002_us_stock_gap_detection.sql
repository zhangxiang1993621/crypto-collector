-- ============================================================
-- 美股数据补缺 RPC
-- 用法：SELECT * FROM get_missing_trading_days('AAPL', '2026-06-10', '2026-06-14');
-- 返回指定 symbol 在日期范围内缺少数据的交易日列表
-- ============================================================

CREATE OR REPLACE FUNCTION get_missing_trading_days(
    p_symbol      TEXT,
    p_start_date  DATE,
    p_end_date    DATE
)
RETURNS TABLE (trade_date DATE) AS $$
DECLARE
    cur_date DATE := p_start_date;
BEGIN
    WHILE cur_date <= p_end_date LOOP
        -- 跳过周末
        IF EXTRACT(DOW FROM cur_date) NOT IN (0, 6) THEN
            -- 检查该 symbol + 日期是否有 K 线数据
            IF NOT EXISTS (
                SELECT 1 FROM us_stock_bars
                WHERE symbol = p_symbol
                  AND bar_time >= cur_date::timestamptz
                  AND bar_time < (cur_date + INTERVAL '1 day')::timestamptz
                LIMIT 1
            ) THEN
                trade_date := cur_date;
                RETURN NEXT;
            END IF;
        END IF;
        cur_date := cur_date + INTERVAL '1 day';
    END LOOP;
END;
$$ LANGUAGE plpgsql STABLE;
