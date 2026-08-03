-- Add columns to the prospects to record the landline vs/ cellphone types
-- The cause: a business number on Google Maps is usually the office landline, 
-- and SMS to a landline is rejected by the carrier
-- Saves money and time -- about $0.008 per number

alter table prospects add column if not exists line_type text default null;
alter table prospects add column if not exists line_type_checked_at timestamptz default null;

select line_type, line_type_checked_at from prospects limit 3;