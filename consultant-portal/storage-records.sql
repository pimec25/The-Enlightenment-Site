insert into storage.buckets (id,name,public,file_size_limit,allowed_mime_types)
values ('consultant-analysis-records','consultant-analysis-records',false,50331648,ARRAY['application/json']);
create policy "consultant records insert own" on storage.objects for insert to authenticated
with check (bucket_id='consultant-analysis-records' and (storage.foldername(name))[1]=(select auth.uid())::text and exists(select 1 from public.profiles p where p.id=(select auth.uid()) and p.role='consultant'));
create policy "consultant records read own" on storage.objects for select to authenticated
using (bucket_id='consultant-analysis-records' and (storage.foldername(name))[1]=(select auth.uid())::text and exists(select 1 from public.profiles p where p.id=(select auth.uid()) and p.role='consultant'));
