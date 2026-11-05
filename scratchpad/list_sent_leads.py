import db

with db.get_conn() as c:
    sent_sends = c.execute('''
        SELECT s.lead_id, l.business_name, l.email, s.sent_at, o.id as outbound_id, o.sent_at as outbound_sent_at, o.to_email
        FROM scheduled_sends s
        JOIN leads l ON l.id = s.lead_id
        LEFT JOIN outbound_messages o ON o.send_id = s.id
        WHERE s.campaign_id = 5 AND s.step_number = 1 AND s.status = 'sent'
        ORDER BY s.sent_at ASC
    ''').fetchall()
    print(f'Total sent leads in step 1 of campaign 5: {len(sent_sends)}')
    for i, r in enumerate(sent_sends, 1):
        print(f"{i}: lead_id={r['lead_id']}, email={r['email']}, to_email={r['to_email']}, sent_at={r['sent_at']}, business={r['business_name']}")

