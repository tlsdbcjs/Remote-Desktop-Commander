//! Bounded GDB/MI parser; no CLI expressions or arbitrary debugger commands.
use racp_contract::RacpError;
use serde_json::{Value,Map,json};
#[derive(Debug)]
pub struct Record {pub token:Option<String>,pub kind:u8,pub name:String,pub data:Value}
struct Parser<'a>{raw:&'a [u8],at:usize,nodes:usize}
fn invalid()->RacpError {RacpError::new("GDB_PROTOCOL_ERROR")}
impl Parser<'_>{
 fn byte(&self)->u8 {self.raw.get(self.at).copied().unwrap_or(0)}
 fn identifier(&mut self)->Result<String,RacpError>{let start=self.at;while self.byte().is_ascii_alphanumeric()||matches!(self.byte(),b'_'|b'-'){self.at+=1;}if self.at==start{return Err(invalid());}String::from_utf8(self.raw[start..self.at].into()).map_err(|_|invalid())}
 fn string(&mut self)->Result<String,RacpError>{if self.byte()!=b'"'{return Err(invalid());}self.at+=1;let mut out=vec![];loop{let ch=self.byte();self.at+=1;match ch{
  0=>return Err(invalid()),b'"'=>return Ok(String::from_utf8_lossy(&out).into_owned()),
  b'\\'=>{let escape=self.byte();self.at+=1;out.push(match escape{b'n'=>10,b'r'=>13,b't'=>9,b'b'=>8,b'f'=>12,b'v'=>11,b'\\'=>92,b'"'=>34,b'0'..=b'7'=>{let mut n=(escape-b'0') as u16;for _ in 0..2{if matches!(self.byte(),b'0'..=b'7'){n=n*8+(self.byte()-b'0') as u16;self.at+=1;}}(n&255) as u8},_=>return Err(invalid())});},
  _=>out.push(ch),}if out.len()>256*1024{return Err(invalid());}}}
 fn result(&mut self,depth:usize)->Result<(String,Value),RacpError>{let key=self.identifier()?;if self.byte()!=b'='{return Err(invalid());}self.at+=1;Ok((key,self.value(depth+1)?))}
 fn value(&mut self,depth:usize)->Result<Value,RacpError>{self.nodes+=1;if depth>32||self.nodes>10000{return Err(invalid());}if self.byte()==b'"'{return Ok(json!(self.string()?));}
  let opening=self.byte();if !matches!(opening,b'['|b'{'){return Err(invalid());}self.at+=1;let closing=if opening==b'['{b']'}else{b'}'};let mut values=vec![];let mut mapping=Map::new();
  while self.byte()!=closing{if opening==b'{'||!matches!(self.byte(),b'"'|b'['|b'{'){let(key,value)=self.result(depth)?;if opening==b'{'{if mapping.insert(key,value).is_some(){return Err(invalid());}}else{values.push(json!({key:value}));}}else{values.push(self.value(depth+1)?);}
   if self.byte()==b','{self.at+=1;}else if self.byte()!=closing{return Err(invalid());}}
  self.at+=1;Ok(if opening==b'['{json!(values)}else{Value::Object(mapping)})}
}
pub fn parse(raw:&[u8])->Result<Record,RacpError>{
 if raw.is_empty()||raw.len()>1024*1024{return Err(invalid());}
 let end=raw.iter().rposition(|b|!matches!(b,b'\r'|b'\n')).ok_or_else(invalid)?;let mut p=Parser{raw:&raw[..=end],at:0,nodes:0};
 let start=p.at;while p.byte().is_ascii_digit(){p.at+=1;}if p.at>20{return Err(invalid());}
 let token=if p.at>start{Some(String::from_utf8_lossy(&p.raw[start..p.at]).into_owned())}else{None};
 let kind=p.byte();p.at+=1;let (name,data)=if matches!(kind,b'~'|b'@'|b'&'){("stream".into(),json!({"text":p.string()?}))}else if matches!(kind,b'^'|b'*'|b'+'|b'='){let name=p.identifier()?;let mut data=Map::new();while p.byte()==b','{p.at+=1;let (key,value)=p.result(0)?;if data.insert(key,value).is_some(){return Err(invalid());}}(name,Value::Object(data))}else{return Err(invalid());};
 if p.at!=p.raw.len(){return Err(invalid());}Ok(Record{token,kind,name,data})
}
